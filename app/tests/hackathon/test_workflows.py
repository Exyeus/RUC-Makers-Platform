import hashlib
import io
import zipfile
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_scopes import scope
from PIL import Image

from eventyay.base.models import Event, User
from eventyay.base.models import Team as OrganizerTeam
from eventyay.hackathon import forms, services
from eventyay.hackathon.companion import Station, safe_extract, validate_manifest
from eventyay.hackathon.models import (
    Announcement,
    Asset,
    AuditEntry,
    DemoSlot,
    Hackathon,
    JoinRequest,
    Participant,
    Project,
    Resource,
    ResourceRead,
    Score,
    StaffAssignment,
    StationToken,
    SupportTicket,
    Team,
    VolunteerTask,
)
from eventyay.hackathon.permissions import is_organizer


def endpoint(h, name, **kwargs):
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': h.slug, **kwargs})


def project_data(**changes):
    data = {
        'title': '真实作品',
        'summary': 'A useful project',
        'description': 'It works.',
        'repository_url': 'https://example.org/repo',
        'demo_url': 'https://example.org/demo',
        'backup_url': 'https://example.org/video',
        'demo_instructions': 'Open demo and click Start',
        'checklist': True,
        'public_consent': True,
        'version': 0,
    }
    data.update(changes)
    return data


def approved_project(h, team, **kwargs):
    data = project_data()
    data.pop('version')
    return Project.objects.create(
        hackathon=h, team=team, state='approved', submitted_at=timezone.now(), equipment_checked=True, **data, **kwargs
    )


def png():
    image = io.BytesIO()
    Image.new('RGB', (40, 40), '#778899').save(image, format='PNG')
    return SimpleUploadedFile('poster.png', image.getvalue(), content_type='image/png')


@pytest.mark.django_db
@pytest.mark.parametrize(
    'name', ['home', 'schedule', 'announcements', 'resources', 'teams', 'gallery', 'stage', 'results']
)
def test_public_pages_render(client, hackathon, name):
    response = client.get(endpoint(hackathon, name))
    assert response.status_code == 200
    assert b'MAKER CAMP' in response.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    'name',
    ['manage', 'participants', 'submissions', 'settings', 'content', 'staff', 'tasks', 'support', 'manage-stage'],
)
def test_organizer_pages_render(client, hackathon, organizer_user, name):
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, name))
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    'name',
    ['manage', 'participants', 'submissions', 'settings', 'content', 'staff', 'tasks', 'manage-stage', 'judging'],
)
def test_participant_cannot_open_staff_pages(client, hackathon, user, name):
    client.force_login(user)
    assert client.get(endpoint(hackathon, name)).status_code == 403


@pytest.mark.django_db
def test_register_and_unique_identity(client, hackathon, user):
    client.force_login(user)
    data = {'name': '新选手', 'school': 'Test', 'contact': 'private', 'consent': True}
    response = client.post(endpoint(hackathon, 'register'), data)
    assert response.status_code == 302
    participant = Participant.objects.get(user=user)
    client.post(endpoint(hackathon, 'register'), {**data, 'name': 'New Name'})
    assert Participant.objects.get(user=user).pk == participant.pk
    assert Participant.objects.count() == 1
    assert client.get(endpoint(hackathon, 'home')).status_code == 200


@pytest.mark.django_db
def test_closed_registration_rejects_new_user(client, hackathon, user):
    hackathon.registration_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'register'), {'name': 'Late', 'school': 'Test', 'contact': 'private', 'consent': True}
    )
    assert response.status_code == 200
    assert not Participant.objects.exists()
    assert '截止' in response.content.decode()


@pytest.mark.django_db
def test_invitation_and_capacity(client, hackathon, team):
    hackathon.team_size = 2
    hackathon.save()
    outsider = User.objects.create_user(email='joiner@example.org')
    person = Participant.objects.create(hackathon=hackathon, user=outsider, name='Joiner')
    client.force_login(outsider)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'invite', 'code': team.invite_code})
    assert response.status_code == 302
    person.refresh_from_db()
    assert person.team_id == team.pk
    third = Participant.objects.create(
        hackathon=hackathon, user=User.objects.create_user(email='third@example.org'), name='Third'
    )
    with pytest.raises(ValidationError, match='已满'):
        services.join_team(hackathon, third, team, invite_code=team.invite_code)


@pytest.mark.django_db
def test_frozen_teams_block_membership_changes(client, hackathon, team, user):
    hackathon.team_freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'rotate'}, follow=True)
    assert '冻结' in response.content.decode()
    assert Team.objects.get(pk=team.pk).invite_code == team.invite_code


@pytest.mark.django_db
def test_team_applications_require_captain(client, hackathon, team):
    applicant = Participant.objects.create(
        hackathon=hackathon, user=User.objects.create_user(email='applicant@example.org'), name='A'
    )
    application = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='I build')
    client.force_login(applicant.user)
    assert (
        client.post(endpoint(hackathon, 'team-action'), {'action': 'accept', 'application': application.pk}).status_code
        == 403
    )
    client.force_login(team.captain.user)
    assert (
        client.post(endpoint(hackathon, 'team-action'), {'action': 'accept', 'application': application.pk}).status_code
        == 302
    )
    applicant.refresh_from_db()
    assert applicant.team_id == team.pk


@pytest.mark.django_db
def test_complete_submission_flow(client, hackathon, team, user):
    client.force_login(user)
    data = project_data()
    assert client.post(endpoint(hackathon, 'project'), data).status_code == 302
    project = Project.objects.get(team=team)
    assert project.revision == 1 and project.state == 'draft'
    response = client.post(endpoint(hackathon, 'upload'), {'kind': 'poster', 'file': png()})
    assert response.status_code == 302
    project.refresh_from_db()
    assert project.revision == 2
    asset = project.assets.get(kind='poster')
    with asset.file.open('rb') as source:
        assert hashlib.sha256(source.read()).hexdigest() == asset.checksum
    assert client.post(endpoint(hackathon, 'project'), project_data(version=2, action='submit')).status_code == 302
    project.refresh_from_db()
    assert project.state == 'submitted' and project.submitted_at
    assert client.get(endpoint(hackathon, 'project')).status_code == 200


@pytest.mark.django_db
def test_incomplete_project_cannot_submit(client, hackathon, team, user):
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'project'), project_data(action='submit'))
    assert response.status_code == 200
    assert '电子项目海报' in response.content.decode()
    assert not Project.objects.exists()


@pytest.mark.django_db
def test_stale_project_does_not_overwrite(client, hackathon, team, user):
    client.force_login(user)
    client.post(endpoint(hackathon, 'project'), project_data())
    response = client.post(endpoint(hackathon, 'project'), project_data(title='Wrong update', version=0))
    assert '其他页面' in response.content.decode()
    assert Project.objects.get(team=team).title == '真实作品'


@pytest.mark.django_db
def test_deadline_blocks_drafts_and_assets(client, hackathon, team, user):
    project = approved_project(hackathon, team)
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'project'), project_data(title='Too late'))
    assert '截止' in response.content.decode()
    client.post(endpoint(hackathon, 'upload'), {'kind': 'poster', 'file': png()})
    assert not project.assets.exists()
    project.refresh_from_db()
    assert project.title == '真实作品'


@pytest.mark.django_db
def test_non_captain_cannot_edit_submission(client, hackathon, team):
    user = User.objects.create_user(email='member@example.org')
    Participant.objects.create(hackathon=hackathon, user=user, name='M', team=team)
    client.force_login(user)
    assert client.post(endpoint(hackathon, 'project'), project_data()).status_code == 403


@pytest.mark.parametrize('address', ['http://localhost:3000', 'http://127.0.0.1', 'http://[::1]', 'http://0.0.0.0'])
def test_localhost_demo_is_rejected(address):
    with pytest.raises(ValidationError):
        forms.validate_demo_url(address)


@pytest.mark.django_db
def test_reject_disguised_upload_and_zip_traversal(hackathon):
    form = forms.AssetForm({'kind': 'poster'}, {'file': SimpleUploadedFile('poster.png', b'<script>alert(1)</script>')})
    assert not form.is_valid()
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('../escape.py', 'bad')
    form = forms.AssetForm({'kind': 'source'}, {'file': SimpleUploadedFile('project.zip', data.getvalue())})
    assert not form.is_valid()


@pytest.mark.django_db
def test_private_assets_and_gallery_consent(client, hackathon, team, user):
    project = approved_project(hackathon, team)
    asset = Asset.objects.create(
        hackathon=hackathon,
        project=project,
        kind='poster',
        file=png(),
        filename='poster.png',
        size=5,
        checksum='a' * 64,
    )
    location = endpoint(hackathon, 'download', asset_id=asset.pk)
    assert client.get(location).status_code == 404
    hackathon.gallery_open = True
    hackathon.save()
    assert client.get(location).status_code == 200
    project.public_consent = False
    project.save()
    assert client.get(location).status_code == 404
    client.force_login(user)
    assert client.get(location).status_code == 200


@pytest.mark.django_db
def test_public_team_cards_never_leak_contact_or_invite(client, hackathon, team, participant):
    body = client.get(endpoint(hackathon, 'teams')).content.decode()
    assert team.invite_code not in body
    assert participant.contact not in body
    assert participant.availability not in body


@pytest.mark.django_db
def test_tenant_isolation(client, hackathon, team, user):
    other_event = Event.objects.create(
        organizer=hackathon.event.organizer, slug='other', name='Other', date_from=timezone.now(), currency='CNY'
    )
    other = Hackathon.objects.create(
        event=other_event,
        slug='other',
        name='Other',
        is_public=True,
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    with scope(event=other_event):
        resource = Resource.objects.create(hackathon=other, title='Secret other resource', category='guide')
    assert client.get(endpoint(hackathon, 'resource', resource_id=resource.pk)).status_code == 404
    client.force_login(user)
    assert (
        client.post(endpoint(other, 'team-action'), {'action': 'invite', 'code': team.invite_code}).status_code == 302
    )
    assert Participant.objects.get(user=user).team_id == team.pk


@pytest.mark.django_db
def test_event_organizer_permission_not_generic_staff(hackathon, user):
    user.is_staff = True
    user.save()
    assert not is_organizer(user, hackathon)
    org_team = OrganizerTeam.objects.create(
        organizer=hackathon.event.organizer, name='Managers', can_change_event_settings=True, all_events=False
    )
    org_team.members.add(user)
    assert not is_organizer(user, hackathon)
    org_team.limit_events.add(hackathon.event)
    assert is_organizer(user, hackathon)


@pytest.mark.django_db
def test_staff_announcements_are_private(client, hackathon, user):
    Announcement.objects.create(hackathon=hackathon, title='INTERNAL NOTE', body='private', audience='staff')
    assert b'INTERNAL NOTE' not in client.get(endpoint(hackathon, 'announcements')).content
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='volunteer')
    client.force_login(user)
    assert b'INTERNAL NOTE' in client.get(endpoint(hackathon, 'announcements')).content


@pytest.mark.django_db
def test_read_progress_is_per_user(client, hackathon, user):
    resource = Resource.objects.create(
        hackathon=hackathon, title='Read this', body='Learn', required=True, category='guide'
    )
    client.force_login(user)
    assert client.post(endpoint(hackathon, 'resource', resource_id=resource.pk)).status_code == 302
    client.post(endpoint(hackathon, 'resource', resource_id=resource.pk))
    assert ResourceRead.objects.count() == 1


@pytest.mark.django_db
def test_judging_requires_assigned_role_and_no_own_team(hackathon, team, user):
    project = approved_project(hackathon, team)
    data = {'innovation': 9, 'implementation': 8, 'impact': 7, 'presentation': 6, 'final': True}
    form = forms.ScoreForm(data)
    assert form.is_valid()
    with pytest.raises(PermissionDenied):
        services.save_score(hackathon, user, project, form)
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='judge')
    with pytest.raises(ValidationError, match='自己的队伍'):
        services.save_score(hackathon, user, project, form)


@pytest.mark.django_db
def test_score_range_weighting_nomination_quota_and_publication(hackathon, team):
    judge = User.objects.create_user(email='judge@example.org')
    StaffAssignment.objects.create(hackathon=hackathon, user=judge, role='judge')
    data = {'innovation': 9, 'implementation': 8, 'impact': 7, 'presentation': 6, 'final': True, 'creativity': True}
    projects = []
    for index in range(4):
        new_team = Team.objects.create(
            hackathon=hackathon, captain=team.captain, name=f'Team {index}', track=team.track
        )
        projects.append(approved_project(hackathon, new_team))
    for project in projects[:3]:
        form = forms.ScoreForm(data)
        assert form.is_valid()
        score = services.save_score(hackathon, judge, project, form)
        assert score.total == 79
    form = forms.ScoreForm(data)
    assert form.is_valid()
    with pytest.raises(ValidationError, match='最多'):
        services.save_score(hackathon, judge, projects[3], form)
    assert not forms.ScoreForm({**data, 'innovation': 11}).is_valid()
    rows = services.results(hackathon)
    assert rows[0]['average'] == 79 and rows[0]['rank'] == rows[1]['rank'] == 1
    assert not rows[-1]['complete']
    hackathon.results_published = True
    hackathon.save()
    with pytest.raises(ValidationError, match='锁定'):
        services.save_score(hackathon, judge, projects[0], form)


@pytest.mark.django_db
def test_results_hidden_until_published(client, hackathon, team):
    project = approved_project(hackathon, team)
    response = client.get(endpoint(hackathon, 'results'))
    assert project.title not in response.content.decode()


@pytest.mark.django_db
def test_stage_transitions_and_single_active_slot(hackathon, team, organizer_user):
    project = approved_project(hackathon, team)
    services.generate_queue(hackathon, organizer_user)
    services.generate_queue(hackathon, organizer_user)
    assert DemoSlot.objects.count() == 1
    slot = project.slot
    with pytest.raises(ValidationError, match='准备就绪'):
        services.stage_action(hackathon, organizer_user, slot, 'start')
    services.stage_action(hackathon, organizer_user, slot, 'ready')
    services.stage_action(hackathon, organizer_user, slot, 'start')
    slot.refresh_from_db()
    assert services.clock_state(hackathon, slot)['phase'] == 'demo'
    services.stage_action(hackathon, organizer_user, slot, 'pause')
    slot.refresh_from_db()
    assert services.clock_state(hackathon, slot)['phase'] == 'paused'
    services.stage_action(hackathon, organizer_user, slot, 'resume')
    slot.refresh_from_db()
    slot.started_at = timezone.now() - timedelta(seconds=125)
    slot.save()
    state = services.clock_state(hackathon, slot)
    assert state['phase'] == 'transition' and 24 <= state['remaining'] <= 25
    services.stage_action(hackathon, organizer_user, slot, 'finish')
    slot.refresh_from_db()
    assert slot.state == 'done'


@pytest.mark.django_db
def test_defer_requires_reason_and_keeps_project(hackathon, team, organizer_user):
    project = approved_project(hackathon, team)
    services.generate_queue(hackathon, organizer_user)
    with pytest.raises(ValidationError):
        services.stage_action(hackathon, organizer_user, project.slot, 'defer')
    services.stage_action(hackathon, organizer_user, project.slot, 'defer', '开学典礼时段冲突')
    assert DemoSlot.objects.get(project=project).note == '开学典礼时段冲突'


@pytest.mark.django_db
def test_station_token_expires_and_checks_organizer(client, hackathon, organizer_user):
    token = 'random-test-token'
    station = StationToken.objects.create(
        hackathon=hackathon,
        digest=hashlib.sha256(token.encode()).hexdigest(),
        created_by=organizer_user,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    location = endpoint(hackathon, 'station-state')
    assert client.get(location).status_code == 403
    assert client.get(location, HTTP_AUTHORIZATION=f'Bearer {token}').status_code == 200
    station.expires_at = timezone.now() - timedelta(seconds=1)
    station.save()
    assert client.get(location, HTTP_AUTHORIZATION=f'Bearer {token}').status_code == 403


@pytest.mark.django_db
def test_volunteer_cannot_modify_others_task(client, hackathon, user):
    other = User.objects.create_user(email='other-volunteer@example.org')
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='volunteer')
    task = VolunteerTask.objects.create(hackathon=hackathon, title='Check sound', owner=other, due_at=timezone.now())
    client.force_login(user)
    assert client.post(endpoint(hackathon, 'tasks'), {'task': task.pk, 'action': 'start'}).status_code == 404
    task.owner = user
    task.save()
    client.post(endpoint(hackathon, 'tasks'), {'task': task.pk, 'action': 'start'})
    client.post(endpoint(hackathon, 'tasks'), {'task': task.pk, 'action': 'done'})
    task.refresh_from_db()
    assert task.finished_at >= task.started_at
    assert task.approved_minutes == 0


@pytest.mark.django_db
def test_support_tickets_are_private(client, hackathon, user):
    other = User.objects.create_user(email='support@example.org')
    SupportTicket.objects.create(
        hackathon=hackathon, user=other, subject='PRIVATE SUBJECT', body='Private', category='tech'
    )
    client.force_login(user)
    assert b'PRIVATE SUBJECT' not in client.get(endpoint(hackathon, 'support')).content


@pytest.mark.django_db
def test_csv_export_protects_formula_cells(client, hackathon, participant, organizer_user):
    participant.name = '=HYPERLINK("bad")'
    participant.save()
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'export', kind='participants'))
    assert response.status_code == 200
    body = response.content.decode('utf-8-sig')
    assert "'=HYPERLINK" in body
    assert participant.code in body


def test_archive_extraction_does_not_escape(tmp_path):
    source = tmp_path / 'attack.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('../escape.txt', 'bad')
    with pytest.raises(ValueError, match='Unsafe'):
        safe_extract(source, tmp_path / 'unpacked')
    assert not (tmp_path / 'escape.txt').exists()


def test_companion_manifest_validation_and_token_origin(tmp_path):
    station = Station('http://127.0.0.1:8000/hackathon/test/station/state/', 'secret', tmp_path)
    with pytest.raises(ValueError, match='another origin'):
        station.request('https://evil.example/steal')
    with pytest.raises(ValueError):
        validate_manifest({'clock': {'phase': 'demo', 'remaining': -1}, 'assets': []})
    assert validate_manifest({'clock': {'phase': 'idle', 'remaining': 120}, 'current': None, 'assets': []})


def test_companion_closes_only_its_process_on_transition(tmp_path):
    station = Station('http://localhost:8000/hackathon/test/station/state/', 'secret', tmp_path)
    station.stop = Mock()
    station.update({'clock': {'phase': 'transition', 'remaining': 30}, 'current': None, 'assets': []})
    station.stop.assert_called()


@pytest.mark.django_db
def test_organizer_intake_requires_reason_and_respects_freeze(client, hackathon, team, organizer_user):
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'intake', team_id=team.pk)
    assert client.get(location).status_code == 200
    assert client.post(location, project_data()).status_code == 200
    assert not Project.objects.exists()
    assert client.post(location, project_data(reason='提交前网络故障，已核对 09:30 的日志')).status_code == 302
    item = Project.objects.get(team=team)
    assert AuditEntry.objects.filter(actor=organizer_user, detail__contains='09:30').exists()
    hackathon.freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    response = client.post(location, project_data(version=item.revision, reason='另一条已经确认的补录原因'))
    assert '最终冻结' in response.content.decode()
    assert Project.objects.get(team=team).revision == item.revision


@pytest.mark.django_db
def test_settings_require_complete_scores_before_publication(client, hackathon, team, organizer_user):
    approved_project(hackathon, team)
    client.force_login(organizer_user)
    data = model_to_dict(hackathon)
    data.update(
        {
            'results_published': True,
            'starts_at': hackathon.event.date_from.isoformat(),
            'ends_at': hackathon.event.date_to.isoformat(),
        }
    )
    response = client.post(endpoint(hackathon, 'settings'), data)
    assert response.status_code == 200
    assert '全部有效评委' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.results_published


@pytest.mark.django_db
def test_ready_slot_is_invalidated_when_material_changes(client, hackathon, team, user, organizer_user):
    item = approved_project(hackathon, team)
    services.generate_queue(hackathon, organizer_user)
    services.stage_action(hackathon, organizer_user, item.slot, 'ready')
    client.force_login(user)
    client.post(endpoint(hackathon, 'upload'), {'kind': 'poster', 'file': png()})
    item.refresh_from_db()
    assert item.state == 'changes'
    assert item.slot.state == 'waiting'
    with pytest.raises(ValidationError):
        services.stage_action(hackathon, organizer_user, item.slot, 'start')


@pytest.mark.django_db
def test_confirmed_score_locks_submission_version(client, hackathon, team, user):
    item = approved_project(hackathon, team)
    judge = User.objects.create_user(email='lockedjudge@example.org')
    Score.objects.create(
        hackathon=hackathon,
        project=item,
        judge=judge,
        innovation=8,
        implementation=8,
        impact=8,
        presentation=8,
        final=True,
    )
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'project'), project_data())
    assert '参评版本' in response.content.decode()
    assert Project.objects.get(pk=item.pk).revision == 0


@pytest.mark.django_db
def test_post_requires_csrf_token(hackathon, user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'register'), {'name': 'CSRF attempt'})
    assert response.status_code == 403
    assert not Participant.objects.exists()


@pytest.mark.django_db
def test_two_ready_slots_cannot_both_be_live(hackathon, team, organizer_user):
    first = approved_project(hackathon, team)
    other_team = Team.objects.create(hackathon=hackathon, captain=team.captain, name='Second team', track=team.track)
    second = approved_project(hackathon, other_team)
    services.generate_queue(hackathon, organizer_user)
    for project in [first, second]:
        services.stage_action(hackathon, organizer_user, project.slot, 'ready')
    services.stage_action(hackathon, organizer_user, first.slot, 'start')
    with pytest.raises(ValidationError, match='先结束当前'):
        services.stage_action(hackathon, organizer_user, second.slot, 'start')
    assert DemoSlot.objects.filter(state='live').count() == 1
