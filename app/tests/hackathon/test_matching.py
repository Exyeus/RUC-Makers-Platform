import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.urls import reverse
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import User
from eventyay.hackathon import matching, services
from eventyay.hackathon.forms import TeamForm
from eventyay.hackathon.matching_forms import ProfileForm
from eventyay.hackathon.models import (
    Hackathon,
    Idea,
    IdeaImplementation,
    MatchingBlock,
    MatchingContact,
    MatchingOutbox,
    MatchingProfile,
    MatchingReceipt,
    MatchIntent,
    Participant,
    ProposalConsent,
    RecommendationRun,
    Team,
    TeamMembership,
    TeamProposal,
    Track,
)


@pytest.fixture
def market(hackathon):
    hackathon.matching_enabled = True
    hackathon.matching_roster_enabled = True
    hackathon.matching_idea_min_chars = 2
    hackathon.team_size = 3
    hackathon.save()
    return hackathon


def player(h, index):
    u = User.objects.create_user(email=f'matching{index}@example.org', fullname=f'伙伴 {index}')
    p = Participant.objects.create(
        hackathon=h, user=u, name=f'伙伴 {index}', admission_state='admitted', contact='private-phone', consent=True
    )
    MatchingProfile.objects.create(
        hackathon=h,
        participant=p,
        skills={'frontend': 1},
        needs={'frontend': 1},
        interests=['校园'],
        slots=[100, 101],
        goal='demo',
        visibility='event',
        opt_in=True,
        batch_opt_in=True,
    )
    return p


def concept(h, author, mode='challenge'):
    return Idea.objects.create(
        hackathon=h,
        author=author,
        title='校园工具',
        problem='需要解决的问题',
        audience='在校学生',
        solution='原型验证',
        mode=mode,
        interests=['校园'],
        role_weights={'frontend': 1},
    )


def proposal(h, members, idea=None, team=None, name=None):
    idea = idea or concept(h, members[0])
    track, _ = Track.objects.get_or_create(hackathon=h, name='开放')
    with transaction.atomic():
        locked = services.lock_event(h)
        return matching.create_proposal(
            locked,
            members[0].user,
            [str(p.pk) for p in members],
            idea.pk,
            track.pk,
            name or f'队伍-{uuid.uuid4().hex[:8]}',
            '两天做出最小 Demo，每人承担一个角色，每晚沟通，退出时主动告知。',
            team.pk if team else None,
        )


def consent(h, p, result, decision='accept', key=None):
    body = {'proposal_id': result['proposal_id'], 'proposal_hash': result['proposal_hash'], 'decision': decision}
    return matching.execute(
        h,
        p.user,
        'consents',
        key or uuid.uuid4().hex,
        body,
        lambda locked: matching.consent_proposal(
            locked, p.user, result['proposal_id'], result['proposal_hash'], decision
        ),
    )


def endpoint(h, operation=None, name='matching-api', **kwargs):
    if operation:
        kwargs['operation'] = operation
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': h.slug, **kwargs})


@pytest.mark.django_db
def test_contact_is_separate_from_roster_and_private(market):
    a, b = player(market, 1), player(market, 2)
    MatchingContact.objects.create(hackathon=market, participant=b, text='explicit-wechat', share_on_accept=True)
    result = matching.send_intent(market, a.user, b.pk, '想一起做校园工具')
    item = MatchIntent.objects.get(pk=result['intent_id'])
    assert matching.shared_contacts(market, a, item) == {}
    matching.decide_intent(market, b.user, item.pk, 'accept', 1)
    item.refresh_from_db()
    assert matching.shared_contacts(market, a, item) == {str(b.pk): 'explicit-wechat'}
    a.refresh_from_db()
    assert a.team_id is None
    assert not MatchingOutbox.objects.filter(kind='TeamMembershipConfirmed').exists()
    assert 'private-phone' not in json.dumps(matching.public_person(b))


@pytest.mark.django_db
def test_full_consent_commits_atomically_and_retry_replays(market):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    assert consent(market, a, result).result['state'] == 'awaiting_consent'
    a.refresh_from_db()
    assert a.team_id is None
    last = consent(market, b, result, key='lost-response')
    replay = consent(market, b, result, key='lost-response')
    assert last.pk == replay.pk
    assert last.result['state'] == 'confirmed'
    assert TeamMembership.objects.filter(left_at=None).count() == 2
    assert MatchingOutbox.objects.filter(kind='TeamMembershipConfirmed').count() == 1
    assert matching.consume_outbox(market) > 0
    assert matching.consume_outbox(market) == 0


@pytest.mark.django_db
def test_changed_body_rejected(market):
    a = player(market, 1)
    matching.execute(market, a.user, 'example', 'same', {'v': 1}, lambda h: {'ok': True})
    with pytest.raises(matching.MatchingError, match='相同请求编号'):
        matching.execute(market, a.user, 'example', 'same', {'v': 2}, lambda h: {'ok': True})


@pytest.mark.django_db
def test_no_impersonation_and_old_hash_rejected(market):
    a, b, outsider = [player(market, i) for i in range(3)]
    result = proposal(market, [a, b])
    with pytest.raises(PermissionDenied):
        consent(market, outsider, result)
    receipt = matching.execute(
        market,
        a.user,
        'consents',
        'wrong-hash',
        {},
        lambda h: matching.consent_proposal(h, a.user, result['proposal_id'], '0' * 64, 'accept'),
    )
    assert receipt.status == 409
    assert not ProposalConsent.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('change', ['profile', 'idea', 'policy', 'admission', 'block'])
def test_stale_snapshot_cannot_enroll(market, change):
    a, b = player(market, 1), player(market, 2)
    idea = concept(market, a)
    result = proposal(market, [a, b], idea)
    consent(market, a, result)
    if change == 'profile':
        MatchingProfile.objects.filter(participant=b).update(version=2)
    elif change == 'idea':
        Idea.objects.filter(pk=idea.pk).update(version=2)
    elif change == 'policy':
        market.matching_policy_version += 1
        market.save()
    elif change == 'admission':
        Participant.objects.filter(pk=b.pk).update(admission_state='withdrawn')
    else:
        MatchingBlock.objects.create(hackathon=market, source=b, target=a)
    last = consent(market, b, result)
    assert last.result['state'] == 'stale'
    assert not Team.objects.exists()


@pytest.mark.django_db
def test_existing_roster_must_all_confirm_and_be_preserved(market):
    a, b, c = [player(market, i) for i in range(3)]
    initial = proposal(market, [a, b])
    consent(market, a, initial)
    final = consent(market, b, initial)
    team = Team.objects.get(pk=final.result['team_id'])
    a.refresh_from_db()
    b.refresh_from_db()
    with pytest.raises(matching.MatchingError, match='所有成员'):
        proposal(market, [a, c], team=team)
    addition = proposal(market, [a, b, c], team=team, name=team.name)
    consent(market, a, addition)
    consent(market, c, addition)
    c.refresh_from_db()
    assert c.team_id is None
    assert consent(market, b, addition).result['state'] == 'confirmed'
    team.refresh_from_db()
    assert team.members.count() == 3
    assert not team.recruiting


@pytest.mark.django_db
def test_pending_admission_cannot_join_but_can_discuss(market):
    a, b = player(market, 1), player(market, 2)
    a.admission_state = 'pending'
    a.save()
    assert matching.send_intent(market, a.user, b.pk, '先讨论想法')['state'] == 'pending'
    with pytest.raises(matching.MatchingError, match='资格'):
        proposal(market, [a, b])


@pytest.mark.django_db
def test_challenge_allows_multiple_implementations_without_author(market):
    author, a, b, c, d = [player(market, i) for i in range(5)]
    idea = concept(market, author)
    for members in ([a, b], [c, d]):
        result = proposal(market, members, idea)
        for p in members:
            consent(market, p, result)
    assert IdeaImplementation.objects.filter(idea=idea).count() == 2
    author.refresh_from_db()
    assert author.team_id is None
    idea.mode = 'recruit'
    idea.save()
    with pytest.raises(matching.MatchingError, match='作者'):
        proposal(market, [author, player(market, 10)], idea=concept(market, a, 'recruit'))


@pytest.mark.django_db
def test_freeze_blocks_join_leave_and_saves_snapshot(market, organizer_user):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    consent(market, a, result)
    matching.execute(market, organizer_user, 'freeze', 'freeze', {}, lambda h: matching.freeze(h, organizer_user))
    assert consent(market, b, result).result['error'] == 'ROSTER_FROZEN'
    assert not Team.objects.exists()
    market.refresh_from_db()
    assert market.matching_frozen_at


@pytest.mark.django_db
def test_old_join_entry_cannot_bypass_consent(market):
    a, b = player(market, 1), player(market, 2)
    track = Track.objects.create(hackathon=market, name='开放')
    team = Team.objects.create(hackathon=market, name='原队', captain=a, track=track)
    with pytest.raises(ValidationError, match='全员确认'):
        services.join_team(market, b, team, invite_code=team.invite_code)


@pytest.mark.django_db
def test_database_rejects_cross_event_and_over_capacity(market, event):
    a, b, c, d = [player(market, i) for i in range(4)]
    other_event = event.__class__.objects.create(
        organizer=event.organizer, name='Other', slug='other', date_from=timezone.now(), currency='CNY'
    )
    other = Hackathon.objects.create(
        event=other_event,
        slug='other',
        name='Other',
        registration_deadline=market.registration_deadline,
        team_freeze_at=market.team_freeze_at,
        soft_deadline=market.soft_deadline,
        submission_deadline=market.submission_deadline,
        freeze_at=market.freeze_at,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchingProfile.objects.create(hackathon=other, participant=a)
    track = Track.objects.create(hackathon=market, name='开放')
    team = Team.objects.create(hackathon=market, name='队伍', captain=a, track=track)
    Participant.objects.filter(pk__in=[a.pk, b.pk, c.pk]).update(team=team)
    with pytest.raises(IntegrityError), transaction.atomic():
        Participant.objects.filter(pk=d.pk).update(team=team)


@pytest.mark.django_db
def test_recommendation_unknown_time_filter_and_block_privacy(market):
    a, b, c = [player(market, i) for i in range(3)]
    MatchingProfile.objects.filter(participant=b).update(slots=None, visibility='recommend')
    MatchingProfile.objects.filter(participant=c).update(slots=[200])
    data = matching.recommendations(market, a)
    assert [r['target_id'] for r in data['results']] == [str(b.pk)]
    assert 'time' in data['results'][0]['unknown_fields']
    assert 'score' not in data['results'][0]
    assert matching.visible_to(a, b)
    assert not matching.visible_to(a, b, discovery=True)
    matching.set_block(market, b.user, a.pk)
    assert matching.recommendations(market, a)['results'] == []
    assert not RecommendationRun.objects.filter(results__contains=[{'target_id': str(b.pk)}]).exists()


@pytest.mark.django_db
def test_batch_three_way_time_and_no_roster_writes(market, organizer_user):
    people = [player(market, i) for i in range(3)]
    for p, slots in zip(people, [[1, 2], [2, 3], [1, 3]]):
        MatchingProfile.objects.filter(participant=p).update(slots=slots)
    concept(market, people[0])
    result = matching.batch_suggestions(market, organizer_user)
    assert result['state'] == 'feasible'
    assert result['diagnostics']['optimality_proven'] is False
    assert all(len(r['member_ids']) == 2 for r in result['results'])
    assert not TeamMembership.objects.exists()
    assert not TeamProposal.objects.exists()


@pytest.mark.django_db
def test_profile_form_uses_absolute_slots_and_learning_mask():
    form = ProfileForm(
        {
            'version': 0,
            'visibility': 'event',
            'opt_in': True,
            'independent_roles': ['design'],
            'learning_roles': ['frontend'],
            'slots': '2026-11-01T19:00+08:00',
            'interests': '校园，教育',
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data['skills'] == {'design': 1}
    assert form.cleaned_data['slots'] == [
        int(timezone.datetime.fromisoformat('2026-11-01T11:00+00:00').timestamp()) // 1800
    ]
    bad = ProfileForm({'version': 0, 'visibility': 'event', 'slots': '2026-11-01T19:00'})
    assert not bad.is_valid()


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['matching', 'matching-profile', 'idea-create', 'proposal-create'])
def test_market_pages_and_contact_secrecy(client, market, name):
    a, b = player(market, 1), player(market, 2)
    concept(market, a)
    matching.send_intent(market, a.user, b.pk, '一起聊聊')
    client.force_login(a.user)
    response = client.get(endpoint(market, name=name))
    assert response.status_code == 200
    assert b'private-phone' not in response.content


@pytest.mark.django_db
def test_operations_permissions_and_receipt_ownership(client, market, organizer_user):
    a, b = player(market, 1), player(market, 2)
    client.force_login(a.user)
    assert client.get(endpoint(market, name='matching-operations')).status_code == 403
    body = {'recipient_id': str(b.pk), 'message': '一起讨论'}
    response = client.post(
        endpoint(market, 'intents'), json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY='request-1'
    )
    assert response.status_code == 200
    response2 = client.post(
        endpoint(market, 'intents'), json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY='request-1'
    )
    assert response.json() == response2.json()
    receipt_id = response.json()['receipt_id']
    client.force_login(b.user)
    assert client.get(endpoint(market, name='matching-receipt', receipt_id=receipt_id)).status_code == 404
    client.force_login(organizer_user)
    assert client.get(endpoint(market, name='matching-operations')).status_code == 200


@pytest.fixture
def transactional_matching_cache(monkeypatch):
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_version_sync', lambda self: None)
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_deleted_sync', lambda self: None)


@pytest.mark.usefixtures('transactional_matching_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_twenty_requests_for_one_vacancy(market):
    a, b = player(market, 1), player(market, 2)
    initial = proposal(market, [a, b])
    consent(market, a, initial)
    team = Team.objects.get(pk=consent(market, b, initial).result['team_id'])
    a.refresh_from_db()
    b.refresh_from_db()
    newcomers = [player(market, i + 10) for i in range(20)]
    proposals = []
    for p in newcomers:
        result = proposal(market, [a, b, p], team=team, name=team.name)
        consent(market, a, result)
        consent(market, b, result)
        proposals.append(result)

    def accept_one(args):
        p, result = args
        close_old_connections()
        try:
            with scope(event=market.event):
                return consent(market, p, result).result['state']
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=20) as pool:
        states = list(pool.map(accept_one, zip(newcomers, proposals)))
    assert states.count('confirmed') == 1
    assert states.count('stale') == 19
    assert Participant.objects.filter(team=team).count() == 3
    assert TeamMembership.objects.filter(team=team, left_at=None).count() == 3


@pytest.mark.django_db
def test_delete_profile_revokes_contact_and_recommendation_artifacts(market):
    a, b = player(market, 1), player(market, 2)
    MatchingContact.objects.create(hackathon=market, participant=a, text='private', share_on_accept=True)
    result = proposal(market, [a, b])
    matching.recommendations(market, b)
    matching.send_intent(market, a.user, b.pk, '讨论一下')
    receipt = matching.execute(
        market, a.user, 'delete-profile', 'delete', {}, lambda h: matching.delete_profile(h, a.user)
    )
    assert receipt.result['state'] == 'deleted'
    assert not MatchingProfile.objects.filter(participant=a).exists()
    assert not MatchingContact.objects.filter(participant=a).exists()
    assert not RecommendationRun.objects.exists()
    assert TeamProposal.objects.get(pk=result['proposal_id']).state == 'stale'
    assert MatchIntent.objects.get(sender=a).state == 'cancelled'


@pytest.mark.django_db
def test_expired_proposal_returns_durable_410(market):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    TeamProposal.objects.filter(pk=result['proposal_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
    receipt = consent(market, a, result)
    assert receipt.status == 410
    assert TeamProposal.objects.get(pk=result['proposal_id']).state == 'expired'
    assert not ProposalConsent.objects.exists()


@pytest.mark.django_db
def test_policy_configuration_is_idempotent_and_can_enable_disabled_event(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    body = {
        'operation': 'configure',
        'idempotency_key': 'policy-1',
        'enabled': 'on',
        'roster': 'on',
        'team_size': 3,
        'idea_min_chars': 20,
    }
    for _ in range(2):
        assert client.post(endpoint(hackathon, name='matching-operations'), body).status_code == 302
    hackathon.refresh_from_db()
    assert hackathon.matching_enabled
    assert hackathon.matching_policy_version == 2
    assert MatchingReceipt.objects.filter(operation='configure').count() == 1


@pytest.mark.django_db
def test_decline_is_not_overridden_by_high_recommendation_score(market):
    a, b = player(market, 1), player(market, 2)
    result = matching.send_intent(market, a.user, b.pk, '聊聊')
    matching.decide_intent(market, b.user, result['intent_id'], 'decline', 1)
    assert matching.recommendations(market, a)['results'] == []
    assert matching.recommendations(market, b)['results'] == []


@pytest.mark.django_db
def test_api_validation_csrf_and_idea_version(client, market):
    a = player(market, 1)
    client.force_login(a.user)
    assert client.post(endpoint(market, 'intents'), '{}', content_type='application/json').status_code == 422
    assert client.post(endpoint(market, 'intents'), '[]', content_type='application/json').status_code == 422
    body = {
        'version': 0,
        'title': '校园工具',
        'problem': '真实问题',
        'audience': '同学',
        'solution': '原型',
        'mode': 'challenge',
        'state': 'open',
        'interest_tags': '校园',
        'roles': ['design'],
    }
    response = client.post(
        endpoint(market, 'ideas'), json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY='idea-1'
    )
    assert response.status_code == 200, response.content
    body['idea_id'] = response.json()['idea_id']
    assert (
        client.post(
            endpoint(market, 'ideas'), json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY='idea-2'
        ).status_code
        == 409
    )
    from_client = client.__class__(enforce_csrf_checks=True)
    from_client.force_login(a.user)
    assert (
        from_client.post(
            endpoint(market, 'delete-profile'), '{}', content_type='application/json', HTTP_IDEMPOTENCY_KEY='delete'
        ).status_code
        == 403
    )


@pytest.mark.usefixtures('transactional_matching_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_same_person_concurrently_confirms_two_teams(market):
    a, b, c = [player(market, i) for i in range(3)]
    left, right = proposal(market, [a, b]), proposal(market, [c, b])
    consent(market, a, left)
    consent(market, c, right)

    def accept_one(result):
        close_old_connections()
        try:
            with scope(event=market.event):
                return consent(market, b, result).result['state']
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(accept_one, [left, right]))
    assert sorted(states) == ['confirmed', 'stale']
    assert TeamMembership.objects.filter(participant=b, left_at=None).count() == 1


@pytest.mark.usefixtures('transactional_matching_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_consent_racing_freeze_is_serializable(market, organizer_user):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    consent(market, a, result)

    def complete():
        close_old_connections()
        try:
            with scope(event=market.event):
                return consent(market, b, result).result
        finally:
            close_old_connections()

    def freeze():
        close_old_connections()
        try:
            with scope(event=market.event):
                return matching.execute(
                    market, organizer_user, 'freeze', 'freeze-race', {}, lambda h: matching.freeze(h, organizer_user)
                ).result
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        committed, frozen = pool.submit(complete), pool.submit(freeze)
        outcome, snapshot = committed.result(), frozen.result()['snapshot']
    if outcome.get('state') == 'confirmed':
        assert len(next(iter(snapshot.values()))['members']) == 2
    else:
        assert outcome['error'] == 'ROSTER_FROZEN'
        assert snapshot == {}


@pytest.mark.django_db
def test_rendered_team_intent_and_block_forms_work(client, market):
    a, b = player(market, 1), player(market, 2)
    track = Track.objects.create(hackathon=market, name='招募赛道')
    team = Team.objects.create(hackathon=market, name='招募小队', captain=b, track=track)
    Participant.objects.filter(pk=b.pk).update(team=team)
    client.force_login(a.user)
    response = client.get(endpoint(market, name='matching'))
    document = BeautifulSoup(response.content, 'html.parser')
    team_input = document.select_one(f'input[name="team_id"][value="{team.pk}"]')
    form = team_input.find_parent('form')
    data = {element['name']: element.get('value', '') for element in form.select('input[name]')}
    data['message'] = '能贡献用户访谈，想一起聊聊。'
    assert len(data['idempotency_key']) <= 100
    assert client.post(endpoint(market, name='matching'), data).status_code == 302
    assert MatchIntent.objects.get(sender=a, recipient=b).team_id == team.pk
    document = BeautifulSoup(client.get(endpoint(market, name='matching')).content, 'html.parser')
    form = document.select_one('input[name="operation"][value="block"]').find_parent('form')
    data = {element['name']: element.get('value', '') for element in form.select('input[name]')}
    assert client.post(endpoint(market, name='matching'), data).status_code == 302
    assert MatchingBlock.objects.filter(source=a, target=b).exists()


@pytest.mark.django_db
def test_legacy_team_members_can_be_explicitly_admitted(market, organizer_user, team, participant):
    receipt = matching.execute(
        market,
        organizer_user,
        'admission',
        'admit-existing',
        {},
        lambda h: matching.set_admission(h, organizer_user, participant.pk, 'admitted'),
    )
    assert receipt.status == 200
    participant.refresh_from_db()
    assert participant.admission_state == 'admitted'
    assert participant.team_id == team.pk
    receipt = matching.execute(
        market,
        organizer_user,
        'admission',
        'withdraw-existing',
        {},
        lambda h: matching.set_admission(h, organizer_user, participant.pk, 'withdrawn'),
    )
    assert receipt.status == 409


@pytest.mark.django_db
def test_admission_capacity_is_independent_from_matching(market, organizer_user):
    a, b = player(market, 1), player(market, 2)
    Participant.objects.filter(pk__in=[a.pk, b.pk]).update(admission_state='pending')
    market.matching_capacity = 1
    market.save(update_fields=['matching_capacity'])
    for p, expected in [(a, 200), (b, 409)]:
        receipt = matching.execute(
            market,
            organizer_user,
            'admission',
            str(p.pk),
            {},
            lambda h: matching.set_admission(h, organizer_user, p.pk, 'admitted'),
        )
        assert receipt.status == expected
    assert not Team.objects.exists()


@pytest.mark.django_db
def test_captain_transfer_and_leave_updates_membership_history(market):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    consent(market, a, result)
    team_id = consent(market, b, result).result['team_id']
    transfer = matching.execute(
        market, a.user, 'transfer', 'handover', {}, lambda h: matching.transfer_captain(h, a.user, b.pk)
    )
    assert transfer.status == 200
    receipt = matching.execute(market, a.user, 'leave', 'leave', {}, lambda h: matching.leave(h, a.user))
    assert receipt.status == 200
    assert TeamMembership.objects.filter(participant=a, left_at__isnull=False).exists()
    assert Team.objects.get(pk=team_id).captain_id == b.pk
    assert Participant.objects.get(pk=a.pk).team_id is None


@pytest.mark.django_db
def test_stale_recruiting_form_cannot_overwrite_new_roster(market):
    a, b = player(market, 1), player(market, 2)
    result = proposal(market, [a, b])
    consent(market, a, result)
    team = Team.objects.get(pk=consent(market, b, result).result['team_id'])
    form = TeamForm(
        {
            'name': team.name,
            'track': team.track_id,
            'intro': '旧页面资料',
            'needs': '',
            'recruiting': True,
            'version': team.roster_version,
        },
        instance=team,
        hackathon=market,
    )
    assert form.is_valid(), form.errors
    Team.objects.filter(pk=team.pk).update(roster_version=team.roster_version + 1)
    with pytest.raises(ValidationError, match='刷新'):
        services.save_team(market, a, form)
    team.refresh_from_db()
    assert team.intro != '旧页面资料'


@pytest.mark.django_db
@pytest.mark.parametrize('blocker', ['viewer', 'author'])
@pytest.mark.parametrize('entrypoint', ['api', 'page'])
def test_blocked_ideas_hidden_from_all_market_entries(client, market, blocker, entrypoint):
    viewer, author, other = [player(market, i) for i in range(3)]
    hidden = concept(market, author)
    hidden.title = 'blocked-hidden-idea'
    hidden.save()
    visible = concept(market, other)
    visible.title = 'unblocked-visible-idea'
    visible.save()
    client.force_login(viewer.user)
    target = endpoint(market, 'ideas') if entrypoint == 'api' else endpoint(market, name='matching')

    before = client.get(target)
    assert before.status_code == 200
    assert hidden.title in before.content.decode()
    assert visible.title in before.content.decode()

    source, recipient = (viewer, author) if blocker == 'viewer' else (author, viewer)
    matching.set_block(market, source.user, recipient.pk)
    after = client.get(target)
    assert after.status_code == 200
    assert hidden.title not in after.content.decode()
    assert visible.title in after.content.decode()
