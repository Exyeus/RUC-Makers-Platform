import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from eventyay.base.models import User
from eventyay.hackathon import stage_requests
from eventyay.hackathon.models import DemoSlot, Participant, Project, StaffAssignment, Team
from eventyay.hackathon.stage_models import StageOrderRequest


pytestmark = pytest.mark.django_db


def endpoint(h, name, **kwargs):
    return reverse('hackathon:' + name, kwargs={'hackathon_slug': h.slug, **kwargs})


def proof():
    output = io.BytesIO()
    Image.new('RGB', (10, 10)).save(output, format='PNG')
    return SimpleUploadedFile('private.png', output.getvalue(), content_type='image/png')


def form(**changes):
    values = {
        'preference': 'early',
        'reason': '已经安排的考试，需要提前结束展示',
        'evidence_url': 'https://example.org/private-proof',
    }
    values.update(changes)
    result = stage_requests.RequestForm(values)
    assert result.is_valid(), result.errors
    return result


def approved(h, team):
    p = Project.objects.create(hackathon=h, team=team, title='作品', state='approved', submitted_at=timezone.now())
    return DemoSlot.objects.create(hackathon=h, project=p, position=1)


def another_team(h, track, position):
    user = User.objects.create_user(email=f'order{position}@example.invalid')
    person = Participant.objects.create(hackathon=h, user=user, name='其他选手', consent=True)
    team = Team.objects.create(hackathon=h, captain=person, name=f'队伍{position}', track=track)
    person.team = team
    person.save()
    p = Project.objects.create(
        hackathon=h, team=team, title=f'作品{position}', state='approved', submitted_at=timezone.now()
    )
    return DemoSlot.objects.create(hackathon=h, project=p, position=position)


def decision(h, item, **changes):
    slots = list(DemoSlot.objects.filter(hackathon=h).select_related('project').order_by('position', 'pk'))
    values = {
        'request_id': item.pk,
        'version': item.version,
        'queue_version': stage_requests.queue_version(slots),
        'decision': 'accepted',
        'position': 1,
        'note': '已核查证明，为你调整到第一位。',
    }
    values.update(changes)
    return values


def test_requires_reason_proof_valid_type_and_time():
    no_proof = stage_requests.RequestForm({'preference': 'early', 'reason': '考试'})
    assert not no_proof.is_valid()
    fake = stage_requests.RequestForm(
        {'preference': 'early', 'reason': '考试'},
        {'evidence': SimpleUploadedFile('fake.png', b'<html>not-image</html>')},
    )
    assert not fake.is_valid()
    oversized = SimpleUploadedFile('big.pdf', b'x')
    oversized.size = stage_requests.EVIDENCE_LIMIT + 1
    invalid = stage_requests.RequestForm({'preference': 'early', 'reason': '考试'}, {'evidence': oversized})
    assert not invalid.is_valid()
    missing_time = stage_requests.RequestForm(
        {'preference': 'time', 'reason': '考试', 'evidence_url': 'https://example.org/proof'}
    )
    assert not missing_time.is_valid()


@pytest.mark.django_db(transaction=True)
def test_private_file_download_authorization(client, hackathon, participant, team, user, organizer_user):
    data = stage_requests.RequestForm({'preference': 'early', 'reason': '私人原因'}, {'evidence': proof()})
    assert data.is_valid(), data.errors
    item = stage_requests.submit_request(hackathon, participant, data)
    address = endpoint(hackathon, 'stage-request-evidence', request_id=item.pk)
    client.force_login(user)
    response = client.get(address)
    assert response.status_code == 200
    assert 'attachment' in response['Content-Disposition']
    assert 'no-store' in response['Cache-Control']
    response.close()
    stranger = User.objects.create_user(email='stranger-order@example.invalid')
    client.force_login(stranger)
    assert client.get(address).status_code == 403
    StaffAssignment.objects.create(hackathon=hackathon, user=stranger, role='volunteer')
    assert client.get(address).status_code == 403
    client.force_login(organizer_user)
    response = client.get(address)
    assert response.status_code == 200
    response.close()
    with pytest.raises(ValueError):
        item.evidence.url


def test_captain_sees_status_but_not_private_teammate_proof(client, hackathon, participant, team, user):
    teammate_user = User.objects.create_user(email='teammate-order@example.invalid')
    teammate = Participant.objects.create(hackathon=hackathon, user=teammate_user, name='队员', team=team, consent=True)
    item = stage_requests.submit_request(
        hackathon, teammate, form(reason='PRIVATE_MEDICAL_REASON', evidence_url='https://example.org/SECRET_PROOF')
    )
    client.force_login(user)
    html = client.get(endpoint(hackathon, 'stage-requests')).content.decode()
    assert item.get_state_display() in html
    assert 'PRIVATE_MEDICAL_REASON' not in html and 'SECRET_PROOF' not in html
    assert client.get(endpoint(hackathon, 'stage-request-evidence', request_id=item.pk)).status_code == 403


def test_accept_moves_queue_and_returns_private_receipt(client, hackathon, participant, team, user, organizer_user):
    own = approved(hackathon, team)
    own.position = 2
    own.save()
    other = another_team(hackathon, team.track, 1)
    item = stage_requests.submit_request(hackathon, participant, form())
    stage_requests.decide_request(hackathon, organizer_user, decision(hackathon, item))
    item.refresh_from_db()
    own.refresh_from_db()
    other.refresh_from_db()
    assert (item.state, item.applied_position, own.position, other.position) == ('accepted', 1, 1, 2)
    assert item.decision_snapshot['before'] and item.decision_snapshot['after']
    client.force_login(user)
    html = client.get(endpoint(hackathon, 'stage-requests')).content.decode()
    assert '已核查证明' in html and '已调整安排' in html


def test_reject_does_not_move_queue_and_organizer_needs_no_participant(
    client, hackathon, participant, team, organizer_user
):
    own = approved(hackathon, team)
    item = stage_requests.submit_request(hackathon, participant, form())
    client.force_login(organizer_user)
    data = decision(hackathon, item, decision='rejected', position='', note='原安排符合比赛要求，目前无法调整。')
    response = client.post(endpoint(hackathon, 'stage-requests-manage'), data)
    assert response.status_code == 302
    item.refresh_from_db()
    own.refresh_from_db()
    assert item.state == 'rejected' and own.position == 1
    assert client.get(endpoint(hackathon, 'stage-requests-manage')).status_code == 200


def test_queue_change_or_team_change_prevents_acceptance(hackathon, participant, team, organizer_user):
    own = approved(hackathon, team)
    item = stage_requests.submit_request(hackathon, participant, form())
    old = decision(hackathon, item)
    own.state = 'ready'
    own.save()
    with pytest.raises(ValidationError, match='队列已更新'):
        stage_requests.decide_request(hackathon, organizer_user, old)
    participant.team = None
    participant.save()
    with pytest.raises(ValidationError, match='已离开'):
        stage_requests.decide_request(hackathon, organizer_user, decision(hackathon, item))
    item.refresh_from_db()
    assert item.state == 'pending'


def test_live_done_results_and_duplicate_guard(hackathon, participant, team, organizer_user):
    own = approved(hackathon, team)
    item = stage_requests.submit_request(hackathon, participant, form())
    with pytest.raises(ValidationError, match='待处理'):
        stage_requests.submit_request(hackathon, participant, form())
    own.state = 'live'
    own.save()
    with pytest.raises(ValidationError, match='已开始'):
        stage_requests.decide_request(hackathon, organizer_user, decision(hackathon, item))
    hackathon.results_published = True
    hackathon.save()
    with pytest.raises(ValidationError, match='已公布'):
        stage_requests.decide_request(hackathon, organizer_user, decision(hackathon, item))


def test_submit_withdraw_and_attendance_visible_only_to_staff(
    client, hackathon, participant, team, user, organizer_user
):
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'stage-requests'),
        {'preference': 'late', 'reason': '需要晚到', 'evidence_url': 'https://example.org/proof'},
    )
    assert response.status_code == 302
    item = StageOrderRequest.objects.get(participant=participant)
    assert (
        client.post(
            endpoint(hackathon, 'stage-requests'), {'action': 'withdraw', 'request_id': str(item.pk)}
        ).status_code
        == 302
    )
    item.refresh_from_db()
    assert item.state == 'withdrawn'
    assert client.get(endpoint(hackathon, 'stage-requests-manage')).status_code == 403
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'stage-requests-manage'))
    assert participant.availability in response.content.decode()


def test_evidence_fsync_failure_cleans_only_new_file(tmp_path, settings, monkeypatch):
    import os

    from eventyay.hackathon.stage_storage import StageEvidenceStorage

    settings.DATA_DIR = str(tmp_path)
    storage = StageEvidenceStorage()
    existing = storage.save('existing/private.png', proof())
    before = set(p for p in tmp_path.rglob('*') if p.is_file())

    def broken_fsync(descriptor):
        raise OSError('synthetic disk flush failure')

    monkeypatch.setattr(os, 'fsync', broken_fsync)
    with pytest.raises(OSError, match='flush failure'):
        storage.save('new/private.png', proof())
    assert set(p for p in tmp_path.rglob('*') if p.is_file()) == before
    assert storage.exists(existing)
    with pytest.raises(FileExistsError):
        storage._save(existing, proof())
    assert storage.exists(existing)


def test_evidence_database_failure_cleans_saved_file(tmp_path, settings, monkeypatch, hackathon, participant, team):
    settings.DATA_DIR = str(tmp_path)
    request_form = stage_requests.RequestForm({'preference': 'early', 'reason': '考试'}, {'evidence': proof()})
    assert request_form.is_valid(), request_form.errors
    before = set(p for p in tmp_path.rglob('*') if p.is_file())

    def broken_save(self, *args, **kwargs):
        raise RuntimeError('synthetic database save failure')

    monkeypatch.setattr(StageOrderRequest, 'save', broken_save)
    with pytest.raises(RuntimeError, match='database save failure'):
        stage_requests.submit_request(hackathon, participant, request_form)
    assert set(p for p in tmp_path.rglob('*') if p.is_file()) == before


def test_evidence_storage_failure_keeps_bound_text(
    client, tmp_path, settings, monkeypatch, hackathon, participant, team, user
):
    import os

    settings.DATA_DIR = str(tmp_path)
    client.force_login(user)

    def broken_fsync(descriptor):
        raise OSError('synthetic disk failure containing internal details')

    monkeypatch.setattr(os, 'fsync', broken_fsync)
    response = client.post(
        endpoint(hackathon, 'stage-requests'), {'preference': 'early', 'reason': '考试安排应保留', 'evidence': proof()}
    )
    assert response.status_code == 200
    assert '证明文件暂时无法保存' in response.content.decode()
    assert '考试安排应保留' in response.content.decode()
    assert 'internal details' not in response.content.decode()
    assert not StageOrderRequest.objects.filter(participant=participant).exists()
    assert not any(p.is_file() for p in tmp_path.rglob('*'))
