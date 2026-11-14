"""Membership and upload handover regressions from participant journey audit."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from eventyay.hackathon import matching, services, uploads
from eventyay.hackathon.forms import TeamForm
from eventyay.hackathon.models import JoinRequest, MatchingProfile, Participant, Project, Team, UploadSession
from tests.hackathon.test_grouping_closure import make_person
from tests.hackathon.test_matching import consent, player, proposal
from tests.hackathon.test_matching import market as market
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def test_dissolve_revokes_invite_and_cannot_be_joined(hackathon, participant, team):
    old_code = team.invite_code
    newcomer = make_person(hackathon, 'newcomer@example.invalid', '新队友')
    services.dissolve_team(hackathon, participant)
    team.refresh_from_db()
    assert team.invite_code != old_code
    with pytest.raises(ValidationError):
        services.join_team(hackathon, newcomer, team, invite_code=old_code)
    # Even a freshly read code cannot resurrect a dissolved team.
    with pytest.raises(ValidationError):
        services.join_team(hackathon, newcomer, team, invite_code=team.invite_code)
    newcomer.refresh_from_db()
    assert newcomer.team_id is None


def test_create_team_invalidates_old_pending_applications(hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.invalid', '申请人')
    old = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='期待合作')
    form = TeamForm(
        {'name': '另起一队', 'track': team.track_id, 'intro': '', 'needs': '', 'recruiting': True},
        hackathon=hackathon,
    )
    assert form.is_valid(), form.errors
    services.save_team(hackathon, applicant, form)
    old.refresh_from_db()
    assert old.state == 'unavailable'
    with pytest.raises(ValidationError):
        services.join_team(hackathon, applicant, team, application=old)


def test_accepting_one_application_closes_others_without_declining(hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.invalid', '申请人')
    other_captain = make_person(hackathon, 'other@example.invalid', '另一队长')
    other = Team.objects.create(hackathon=hackathon, captain=other_captain, track=team.track, name='另一队伍')
    other_captain.team = other
    other_captain.save()
    accepted = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='期待合作')
    pending = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=other, message='期待合作')
    services.join_team(hackathon, applicant, team, application=accepted)
    accepted.refresh_from_db()
    pending.refresh_from_db()
    assert accepted.state == 'accepted'
    assert pending.state == 'unavailable'


def test_stale_application_does_not_replace_a_valid_invite(hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.invalid', '申请人')
    application = JoinRequest.objects.create(
        hackathon=hackathon, participant=applicant, team=team, message='已撤回', state='withdrawn'
    )
    with pytest.raises(ValidationError):
        services.join_team(hackathon, applicant, team, application=application)
    with pytest.raises(PermissionDenied):
        services.join_team(hackathon, applicant, team, invite_code='invalid')


def test_captain_handover_waits_until_active_upload_is_cancelled(hackathon, participant, team):
    successor = make_person(hackathon, 'successor@example.invalid', '接任队长')
    successor.team = team
    successor.save()
    project = Project.objects.create(hackathon=hackathon, team=team, title='作品')
    task = uploads.start_upload(
        hackathon,
        participant.user,
        project,
        {'id': uuid.uuid4(), 'kind': 'slides', 'filename': 'slides.pdf', 'size': 100, 'checksum': 'a' * 64},
    )
    with pytest.raises(ValidationError, match='完成或取消上传'):
        services.transfer_captain(hackathon, participant, successor)
    team.refresh_from_db()
    assert team.captain_id == participant.pk
    uploads.cancel_upload(task)
    result = services.transfer_captain(hackathon, participant, successor)
    assert result.captain_id == successor.pk
    task.refresh_from_db()
    assert task.user_id == participant.user_id


def test_formal_handover_uses_the_same_upload_guard(market, team):
    successor = player(market, 4)
    successor.team = team
    successor.save()
    project = Project.objects.create(hackathon=market, team=team, title='作品')
    task = UploadSession.objects.create(
        hackathon=market,
        project=project,
        user=team.captain.user,
        kind='slides',
        original_filename='slides.pdf',
        size=100,
        expected_checksum='a' * 64,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    receipt = matching.execute(
        market,
        team.captain.user,
        'transfer',
        'active-upload',
        {},
        lambda h: matching.transfer_captain(h, team.captain.user, successor.pk),
    )
    assert receipt.status == 409
    assert receipt.result['error'] == 'TRANSFER_BLOCKED'
    team.refresh_from_db()
    assert team.captain_id != successor.pk
    assert UploadSession.objects.get(pk=task.pk).state == 'uploading'


def test_last_formal_member_cannot_abandon_existing_project(market, team):
    project = Project.objects.create(hackathon=market, team=team, title='已有作品')
    receipt = matching.execute(
        market, team.captain.user, 'leave', 'has-project', {}, lambda h: matching.leave(h, team.captain.user)
    )
    assert receipt.status == 409
    assert receipt.result['error'] == 'PROJECT_HANDOVER_REQUIRED'
    assert Participant.objects.get(pk=team.captain_id).team_id == team.pk
    assert Project.objects.get(pk=project.pk).team_id == team.pk


def test_formal_roster_confirmation_closes_old_join_applications(market, team):
    a, b = player(market, 1), player(market, 2)
    old = JoinRequest.objects.create(hackathon=market, participant=a, team=team, message='旧申请')
    result = proposal(market, [a, b])
    assert consent(market, a, result).status == 200
    assert consent(market, b, result).status == 200
    old.refresh_from_db()
    assert old.state == 'unavailable'


def test_archived_track_invalidates_unfinished_new_team_proposal(market):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    from eventyay.hackathon.models import TeamProposal

    item = TeamProposal.objects.get(pk=result['proposal_id'])
    item.track.state = 'archived'
    item.track.save()
    receipt = consent(market, a, result)
    assert receipt.status == 409
    assert receipt.result['error'] == 'TRACK_NOT_AVAILABLE'
    assert receipt.result['state'] == 'stale'
    assert not Team.objects.filter(hackathon=market).exists()


def test_public_intro_revocation_keeps_matching_fields_separate(hackathon, participant):
    profile = MatchingProfile.objects.create(
        hackathon=hackathon,
        participant=participant,
        visibility='event',
        opt_in=True,
        public_fields=['skills'],
        skills={'frontend': 2},
    )
    participant.tagline = '我的私人介绍'
    participant.bio = '不再向伙伴公开的经历'
    participant.looking_for_team = False
    participant.save()
    payload = matching.public_person(participant, profile)
    assert payload['name'] == participant.name
    assert payload['tagline'] == payload['bio'] == ''
    assert payload['skills'] == {'frontend': 2}
    participant.looking_for_team = True
    assert matching.public_person(participant, profile)['bio'] == '不再向伙伴公开的经历'


def test_hidden_team_is_not_silently_republished_by_formal_member_leave(market, team):
    member = player(market, 5)
    member.team = team
    member.save()
    team.recruiting = False
    team.save()
    receipt = matching.execute(
        market, member.user, 'leave', 'private-team', {}, lambda h: matching.leave(h, member.user)
    )
    assert receipt.status == 200
    team.refresh_from_db()
    assert not team.recruiting


def test_dissolve_then_recreate_team_can_rotate_current_invite(client, hackathon, participant, team):
    services.dissolve_team(hackathon, participant)
    form = TeamForm(
        {'name': '再组一队', 'track': team.track_id, 'intro': '', 'needs': '', 'recruiting': True},
        hackathon=hackathon,
    )
    assert form.is_valid(), form.errors
    recreated = services.save_team(hackathon, participant, form)
    old_code = recreated.invite_code
    client.force_login(participant.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'rotate'})
    assert response.status_code == 302
    recreated.refresh_from_db()
    assert recreated.invite_code != old_code
