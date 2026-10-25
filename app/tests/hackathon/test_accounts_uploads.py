import asyncio
import hashlib
import io
import os
import struct
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections
from django.test import AsyncRequestFactory, Client
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event, User
from eventyay.hackathon import forms, services, uploads
from eventyay.hackathon.constants import CHUNK_SIZE, MAX_UPLOAD_SIZE
from eventyay.hackathon.downloads import stream_file
from eventyay.hackathon.models import Asset, Hackathon, PrivateStorage, Project, StudentAccount, UploadSession
from tests.hackathon.test_workflows import endpoint, png, project_data


pytestmark = pytest.mark.django_db
PASSWORD = 'Student!Password2026'
PDF = b'%PDF-1.7\n' + b'demo file contents\n' * 80


@pytest.fixture(autouse=True)
def isolated_storage(settings, tmp_path, monkeypatch):
    settings.DATA_DIR = str(tmp_path)
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', PrivateStorage())
    cache.clear()


@pytest.fixture
def project(hackathon, team):
    data = project_data()
    data.pop('version')
    return Project.objects.create(hackathon=hackathon, team=team, **data)


def payload(data=PDF, **changes):
    values = {
        'id': uuid.uuid4(),
        'kind': 'slides',
        'filename': '我的演示.pdf',
        'size': len(data),
        'checksum': hashlib.sha256(data).hexdigest(),
    }
    values.update(changes)
    return values


def begin(client, hackathon, user, data=PDF, **changes):
    client.force_login(user)
    response = client.post(endpoint(hackathon, 'upload-start'), payload(data, **changes))
    assert response.status_code == 200, response.content
    return response.json()


def send_chunk(client, task, data, offset=0):
    return client.post(task['chunk_url'], data, content_type='application/octet-stream', HTTP_UPLOAD_OFFSET=str(offset))


def complete(client, hackathon, user, data=PDF, **changes):
    task = begin(client, hackathon, user, data, **changes)
    assert send_chunk(client, task, data).status_code == 200
    response = client.post(task['finish_url'])
    assert response.status_code == 200, response.content
    return response.json()


def test_student_register_login_without_email(client, hackathon):
    response = client.post(
        endpoint(hackathon, 'student-signup'),
        {
            'student_id': 'ab20260001',
            'name': '同学',
            'password': PASSWORD,
            'password_confirm': PASSWORD,
            'consent': 'on',
        },
    )
    assert response.status_code == 302
    account = StudentAccount.objects.get(student_id='AB20260001')
    assert account.user.email is None
    assert account.user.check_password(PASSWORD)
    assert account.user.password != PASSWORD
    assert not account.user.is_staff and not account.user.is_administrator
    assert client.get(endpoint(hackathon, 'register')).status_code == 200
    client.logout()
    response = client.post(endpoint(hackathon, 'student-login'), {'student_id': 'ab20260001', 'password': PASSWORD})
    assert response.status_code == 302
    assert str(client.session['_auth_user_id']) == str(account.user_id)


@pytest.mark.parametrize(
    'student_id,password,confirm',
    [('abc', PASSWORD, PASSWORD), ('12345678', '1234567890', '1234567890'), ('12345678', PASSWORD, 'wrong')],
)
def test_invalid_student_signup(client, hackathon, student_id, password, confirm):
    response = client.post(
        endpoint(hackathon, 'student-signup'),
        {'student_id': student_id, 'name': '同学', 'password': password, 'password_confirm': confirm, 'consent': 'on'},
    )
    assert response.status_code == 200
    assert not StudentAccount.objects.exists()


def test_duplicate_student_and_inactive_login(client, hackathon, user):
    StudentAccount.objects.create(student_id='20260001', user=user)
    response = client.post(
        endpoint(hackathon, 'student-signup'),
        {'student_id': '20260001', 'name': '同学', 'password': PASSWORD, 'password_confirm': PASSWORD, 'consent': 'on'},
    )
    assert '已注册' in response.content.decode()
    assert StudentAccount.objects.count() == 1
    user.is_active = False
    user.save()
    client.post(endpoint(hackathon, 'student-login'), {'student_id': '20260001', 'password': 'a-strong-test-password'})
    assert '_auth_user_id' not in client.session


def test_student_login_throttle_and_safe_redirect(client, hackathon, user):
    StudentAccount.objects.create(student_id='20260001', user=user)
    for _ in range(15):
        client.post(endpoint(hackathon, 'student-login'), {'student_id': '20260001', 'password': 'wrong'})
    assert (
        client.post(endpoint(hackathon, 'student-login'), {'student_id': '20260001', 'password': 'wrong'}).status_code
        == 429
    )
    cache.clear()
    response = client.post(
        endpoint(hackathon, 'student-login') + '?next=https://example.org/phishing',
        {'student_id': '20260001', 'password': 'a-strong-test-password'},
    )
    assert response.status_code == 302
    assert 'example.org' not in response.url


def test_student_two_factor_is_preserved(client, hackathon, user):
    StudentAccount.objects.create(student_id='20260001', user=user)
    user.require_2fa = True
    user.save()
    response = client.post(
        endpoint(hackathon, 'student-login'), {'student_id': '20260001', 'password': 'a-strong-test-password'}
    )
    assert response.status_code == 302
    assert '_auth_user_id' not in client.session
    assert client.session['eventyay_auth_2fa_user'] == user.pk


def test_profile_keywords_and_public_search(client, hackathon, participant, user):
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'register'),
        {
            'name': '组队测试同学',
            'school': '测试学校',
            'contact': 'PRIVATE_PHONE',
            'keywords': ['Python', 'UI / UX'],
            'tagline': '想用数据解决食堂排队问题',
            'looking_for_team': 'on',
            'consent': 'on',
        },
    )
    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.keywords == ['Python', 'UI / UX']
    client.logout()
    body = client.get(endpoint(hackathon, 'teams'), {'keyword': 'Python', 'q': '食堂'}).content.decode()
    assert participant.name in body and participant.tagline in body and 'PRIVATE_PHONE' not in body
    assert participant.name not in client.get(endpoint(hackathon, 'teams'), {'keyword': '前端'}).content.decode()
    participant.looking_for_team = False
    participant.save()
    assert participant.name not in client.get(endpoint(hackathon, 'teams'), {'keyword': 'Python'}).content.decode()


def test_profile_rejects_unknown_keywords_and_allows_many_known_keywords():
    base = {'name': '同学', 'school': '学校', 'contact': 'contact', 'consent': True}
    assert not forms.ParticipantForm({**base, 'keywords': ['invented']}).is_valid()
    assert forms.ParticipantForm({**base, 'keywords': list(forms.KEYWORDS[:7])}).is_valid()


@pytest.mark.usefixtures('transactional_cache')
def test_chunk_resume_replay_and_complete_idempotency(
    client, hackathon, project, user, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        task = begin(client, hackathon, user)
        assert send_chunk(client, task, PDF[:100]).json()['offset'] == 100
        assert send_chunk(client, task, PDF[:100]).json()['offset'] == 100
        assert send_chunk(client, task, b'x' * 100).status_code == 409
        assert send_chunk(client, task, b'x', 120).status_code == 409
        assert client.post(task['finish_url']).status_code == 409
        resumed = begin(client, hackathon, user)
        assert resumed['id'] == task['id'] and resumed['offset'] == 100
        assert send_chunk(client, task, PDF[100:], 100).status_code == 200
        result = client.post(task['finish_url'])
        assert result.status_code == 200, result.content
        asset = Asset.objects.get(project=project)
        assert asset.original_filename == '我的演示.pdf'
        assert asset.filename.startswith(project.team.code + '_' + project.code + '_slides_')
        assert f'/{project.team_id}/{project.pk}/slides/' in asset.file.name
        with asset.file.open('rb') as source:
            assert source.read() == PDF
        project.refresh_from_db()
        revision = project.revision
        assert client.post(task['finish_url']).status_code == 200
        project.refresh_from_db()
        assert project.revision == revision and Asset.objects.filter(project=project).count() == 1
    assert not uploads.part_path(UploadSession.objects.get(pk=task['id'])).exists()


def test_overlapping_upload_and_identical_reupload(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    assert client.post(endpoint(hackathon, 'upload-start'), payload(PDF + b'changed')).status_code == 409
    assert send_chunk(client, task, PDF).status_code == 200
    assert client.post(task['finish_url']).status_code == 200
    project.refresh_from_db()
    revision = project.revision
    complete(client, hackathon, user)
    project.refresh_from_db()
    assert project.revision == revision
    assert Asset.objects.filter(project=project).count() == 1


def test_changed_idempotency_key_rejected(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    assert client.post(endpoint(hackathon, 'upload-start'), payload(PDF + b'changed', id=task['id'])).status_code == 409


def test_checksum_failure_preserves_previous_asset(client, hackathon, project, user):
    complete(client, hackathon, user)
    previous = Asset.objects.get(project=project)
    task = begin(client, hackathon, user, PDF + b'changed')
    assert send_chunk(client, task, PDF + b'incorre').status_code == 200
    assert client.post(task['finish_url']).status_code == 409
    previous.refresh_from_db()
    assert previous.checksum == hashlib.sha256(PDF).hexdigest()


def test_crash_tail_and_partial_chunk_recovery(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    session = UploadSession.objects.get(pk=task['id'])
    uploads.append_chunk(session, io.BytesIO(PDF[:100]), 0, 100)
    with uploads.part_path(session).open('ab') as target:
        target.write(b'uncommitted bytes')
    with pytest.raises(ValidationError):
        uploads.append_chunk(session, io.BytesIO(PDF[100:110]), 100, 100)
    session.refresh_from_db()
    assert session.offset == 100
    assert send_chunk(client, task, PDF[100:], 100).status_code == 200
    assert client.post(task['finish_url']).status_code == 200


def test_pending_upload_blocks_submit_and_cancel_unblocks(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    assert any('正在上传' in error for error in services.submission_errors(project))
    assert client.post(task['cancel_url']).status_code == 200
    assert not any('正在上传' in error for error in services.submission_errors(project))
    assert send_chunk(client, task, PDF).status_code == 409
    assert client.post(task['cancel_url']).status_code == 200


def test_expiry_deadline_and_ownership(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    stranger = User.objects.create_user(email='stranger@example.org')
    client.force_login(stranger)
    assert client.get(task['status_url']).status_code == 404
    assert send_chunk(client, task, PDF).status_code == 404
    client.force_login(user)
    UploadSession.objects.filter(pk=task['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert send_chunk(client, task, PDF).status_code == 409
    replacement = begin(client, hackathon, user)
    assert replacement['id'] != task['id']
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    assert send_chunk(client, replacement, PDF).status_code == 200
    hackathon.freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    assert client.post(replacement['finish_url']).status_code == 400


def test_upload_limits(client, hackathon, project, user):
    client.force_login(user)
    for values in [
        payload(size=MAX_UPLOAD_SIZE + 1),
        payload(kind='poster', filename='x.png', size=21 * 1024 * 1024),
        payload(filename='x.exe'),
    ]:
        assert client.post(endpoint(hackathon, 'upload-start'), values).status_code == 400
    task = begin(client, hackathon, user)
    with pytest.raises(ValidationError):
        uploads.append_chunk(UploadSession.objects.get(pk=task['id']), io.BytesIO(), 0, CHUNK_SIZE + 1)


def test_storage_failure_retry_preserves_state(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    send_chunk(client, task, PDF)
    with patch('eventyay.hackathon.uploads.os.link', side_effect=OSError('Disk full')):
        assert client.post(task['finish_url']).status_code == 503
    assert not Asset.objects.filter(project=project).exists()
    assert client.post(task['finish_url']).status_code == 200


def test_repeat_project_post_has_single_revision(client, hackathon, team, user):
    client.force_login(user)
    for _ in range(2):
        assert client.post(endpoint(hackathon, 'project'), project_data()).status_code == 302
    project = Project.objects.get(team=team)
    assert project.revision == 1
    client.post(endpoint(hackathon, 'upload'), {'kind': 'poster', 'file': png()})
    for _ in range(2):
        assert client.post(endpoint(hackathon, 'project'), project_data(version=2, action='submit')).status_code == 302
    project.refresh_from_db()
    assert project.revision == 3


def test_byte_range_download_and_access(client, hackathon, project, user):
    result = complete(client, hackathon, user)
    response = client.get(result['download_url'], HTTP_RANGE='bytes=10-39')
    assert response.status_code == 206
    assert b''.join(response.streaming_content) == PDF[10:40]
    assert response['Content-Length'] == '30'
    assert response['ETag'] == '"' + hashlib.sha256(PDF).hexdigest() + '"'
    assert client.head(result['download_url'])['Content-Length'] == str(len(PDF))
    assert client.get(result['download_url'], HTTP_RANGE='bytes=999999-').status_code == 416
    assert b''.join(client.get(result['download_url'], HTTP_RANGE='bytes=-12').streaming_content) == PDF[-12:]
    client.logout()
    assert client.get(result['download_url'], HTTP_RANGE='bytes=0-10').status_code == 404


def test_asgi_download_streams_in_bounded_blocks():
    data = b'x' * (1024 * 1024)
    request = AsyncRequestFactory().get('/file')
    response = stream_file(request, io.BytesIO(data), len(data), 'test.mp4')
    assert response.is_async

    async def consume():
        size = 0
        async for block in response.streaming_content:
            assert len(block) <= 256 * 1024
            size += len(block)
        return size

    assert asyncio.run(consume()) == len(data)


def test_upload_csrf_required(hackathon, user, project):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    assert client.post(endpoint(hackathon, 'upload-start'), payload()).status_code == 403


@pytest.fixture
def transactional_cache(monkeypatch):
    # The video Redis cache hook expects its own async test harness. Upload
    # concurrency exercises real PostgreSQL locks, not that unrelated cache.
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_version_sync', lambda self: None)
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_deleted_sync', lambda self: None)


@pytest.mark.usefixtures('transactional_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_concurrent_same_chunk_and_finalize(hackathon, project, user):
    session = uploads.start_upload(hackathon, user, project, payload())
    barrier = Barrier(2)

    def append():
        close_old_connections()
        try:
            with scope(event=hackathon.event):
                barrier.wait(timeout=10)
                return uploads.append_chunk(session, io.BytesIO(PDF), 0, len(PDF)).offset
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: append(), range(2))) == [len(PDF), len(PDF)]
    barrier = Barrier(2)

    def finish():
        close_old_connections()
        try:
            with scope(event=hackathon.event):
                barrier.wait(timeout=10)
                return uploads.finish_upload(session).state
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: finish(), range(2))) == ['complete', 'complete']
    project.refresh_from_db()
    assert project.revision == 1
    assert Asset.objects.filter(project=project).count() == 1


def test_student_password_reset_invalidates_sessions(hackathon, user):

    StudentAccount.objects.create(student_id='20260001', user=user)
    original = user.get_session_auth_hash()
    with patch(
        'eventyay.hackathon.management.commands.reset_student_password.getpass', side_effect=[PASSWORD, PASSWORD]
    ):
        call_command('reset_student_password', '20260001')
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert user.get_session_auth_hash() != original


def test_staff_role_accepts_student_id(hackathon, user):
    StudentAccount.objects.create(student_id='AB20260001', user=user)
    form = forms.StaffForm({'email': 'ab20260001', 'role': 'volunteer'})
    assert form.is_valid(), form.errors
    assert form.staff_user == user


def test_completed_old_upload_reports_replacement(client, hackathon, project, user):
    original = complete(client, hackathon, user)
    complete(client, hackathon, user, PDF + b'new version')
    assert client.get(original['status_url']).json()['state'] == 'superseded'


def test_foreign_event_cannot_access_upload(client, hackathon, project, user):
    task = begin(client, hackathon, user)

    event = Event.objects.create(
        organizer=hackathon.event.organizer, name='Other', slug='other-upload', date_from=timezone.now(), currency='CNY'
    )
    other = Hackathon.objects.create(
        event=event,
        slug='other-upload',
        name='Other',
        is_public=True,
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    assert client.get(task['status_url'].replace(f'/{hackathon.slug}/', f'/{other.slug}/')).status_code == 404


def test_zip_metadata_checked_before_allocation():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('project/README.md', 'A real project')
    form = forms.AssetForm({'kind': 'source'}, {'file': SimpleUploadedFile('project.zip', buffer.getvalue())})
    assert form.is_valid(), form.errors
    invalid = bytearray(buffer.getvalue())
    end = invalid.rfind(b'PK\x05\x06')
    struct.pack_into('<L', invalid, end + 12, 500 * 1024 * 1024)
    with patch('eventyay.hackathon.forms.zipfile.ZipFile') as parser:
        form = forms.AssetForm({'kind': 'source'}, {'file': SimpleUploadedFile('project.zip', bytes(invalid))})
        assert not form.is_valid()
        parser.assert_not_called()


def test_download_disconnect_before_first_byte_closes_file():
    source = io.BytesIO(PDF)
    response = stream_file(AsyncRequestFactory().get('/file'), source, len(PDF), 'slides.pdf')
    response.close()
    assert source.closed


def test_cleanup_keeps_referenced_materials(hackathon, project, user, tmp_path):

    root = Path(PrivateStorage().location)
    asset = Asset.objects.create(
        hackathon=hackathon,
        project=project,
        kind='slides',
        file=SimpleUploadedFile('slides.pdf', PDF),
        filename='slides.pdf',
        size=len(PDF),
        checksum=hashlib.sha256(PDF).hexdigest(),
    )
    orphan = root / str(hackathon.pk) / 'orphan.pdf'
    orphan.write_bytes(PDF)
    old = (timezone.now() - timedelta(days=3)).timestamp()
    os.utime(orphan, (old, old))
    os.utime(asset.file.path, (old, old))
    call_command('cleanup_hackathon_uploads', dry_run=True)
    assert orphan.exists()
    call_command('cleanup_hackathon_uploads')
    assert not orphan.exists()
    assert Path(asset.file.path).exists()


def test_intent_precedes_hash_and_fixed_expiry(client, hackathon, project, user):
    clicked = timezone.now() - timedelta(seconds=2)
    task = begin(client, hackathon, user, checksum='', client_clicked_at=clicked.isoformat())
    evidence = signing.loads(task['receipt'], salt='hackathon.upload-receipt')
    assert evidence['expected_checksum'] == ''
    assert evidence['client_clicked_at'] == clicked.isoformat()
    assert task['state'] == 'hashing'
    assert send_chunk(client, task, PDF).status_code == 409
    original_expiry = task['expires_at']
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    assert client.post(endpoint(hackathon, 'upload-start'), payload()).status_code == 400
    bound = client.post(endpoint(hackathon, 'upload-start'), payload(id=task['id'])).json()
    assert bound['checksum_bound_at'] and bound['expected_checksum'] == hashlib.sha256(PDF).hexdigest()
    assert bound['received_at'] == task['received_at']
    assert send_chunk(client, task, PDF).status_code == 200
    result = client.post(task['finish_url']).json()
    assert result['state'] == 'complete' and result['expires_at'] == original_expiry
    assert 'attachment' in client.get(task['status_url'] + '?download=1')['Content-Disposition']


def test_bound_hash_cannot_change_after_deadline(client, hackathon, project, user):
    task = begin(client, hackathon, user)
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    changed = payload(id=task['id'], checksum='a' * 64)
    assert client.post(endpoint(hackathon, 'upload-start'), changed).status_code == 409
    assert UploadSession.objects.get(pk=task['id']).expected_checksum == hashlib.sha256(PDF).hexdigest()


def test_client_time_does_not_bypass_deadline(client, hackathon, project, user):
    client.force_login(user)
    clicked = timezone.now() - timedelta(days=1)
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    assert (
        client.post(endpoint(hackathon, 'upload-start'), payload(client_clicked_at=clicked.isoformat())).status_code
        == 400
    )
    assert not UploadSession.objects.filter(project=project).exists()


def queue_project(client, hackathon, project):
    project.refresh_from_db()
    response = client.post(
        endpoint(hackathon, 'project'),
        project_data(version=project.revision, action='submit'),
        HTTP_ACCEPT='application/json',
    )
    assert response.status_code == 200, response.content
    assert response.json()['state'] == 'queued'
    return response.json()


def test_queued_submission_auto_completes_after_deadline(client, hackathon, project, user):
    poster = png().read()
    task = begin(client, hackathon, user, poster, kind='poster', filename='poster.png')
    queued = queue_project(client, hackathon, project)
    evidence = signing.loads(queued['receipt'], salt='hackathon.submission-receipt')
    assert evidence['manifest']['uploads'][0]['id'] == task['id']
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    assert send_chunk(client, task, poster).status_code == 200
    assert client.post(task['finish_url']).status_code == 200
    project.refresh_from_db()
    assert project.state == 'submitted' and project.submitted_at
    status = client.get(endpoint(hackathon, 'project-status')).json()
    assert status['receipt_id'] == queued['receipt_id']
    assert status['manifest_sha256'] == queued['manifest_sha256']
    assert '服务器登记' in client.get(endpoint(hackathon, 'project')).content.decode()


def test_queued_submission_locks_metadata_and_new_files(client, hackathon, project, user):
    task = begin(client, hackathon, user, png().read(), kind='poster', filename='poster.png')
    queue_project(client, hackathon, project)
    project.refresh_from_db()
    response = client.post(
        endpoint(hackathon, 'project'),
        project_data(version=project.revision, title='替换信息'),
        HTTP_ACCEPT='application/json',
    )
    assert response.status_code == 400
    assert client.post(endpoint(hackathon, 'upload-start'), payload()).status_code == 400
    assert client.post(task['cancel_url']).status_code == 200
    project.refresh_from_db()
    assert project.state == 'changes' and project.submitted_at is None


def test_expired_queue_never_submits_and_receipt_stays(client, hackathon, project, user):
    task = begin(client, hackathon, user, png().read(), kind='poster', filename='poster.png')
    queued = queue_project(client, hackathon, project)
    UploadSession.objects.filter(pk=task['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(task['status_url']).json()['state'] == 'expired'
    assert '过期' in client.get(endpoint(hackathon, 'project-status')).json()['label']
    assert client.post(task['finish_url']).status_code == 409
    uploads.expire_uploads(hackathon)
    project.refresh_from_db()
    assert project.state == 'changes' and str(project.submission_receipt) == queued['receipt_id']


def test_admission_wait_and_expired_lease_recovery(client, hackathon, project, user):
    first = begin(client, hackathon, user)
    second = begin(client, hackathon, user, png().read(), kind='poster', filename='poster.png')
    with patch('eventyay.hackathon.uploads.UPLOAD_SLOTS', 1):
        assert client.post(first['admit_url']).status_code == 200
        response = client.post(second['admit_url'])
        assert response.status_code == 429 and response['Retry-After'] == '5'
        assert send_chunk(client, second, png().read()).status_code == 429
        assert UploadSession.objects.get(pk=second['id']).offset == 0
        UploadSession.objects.filter(pk=first['id']).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        assert client.post(second['admit_url']).status_code == 200


def test_low_disk_keeps_intent_and_waits(client, hackathon, project, user):
    with patch('eventyay.hackathon.uploads.shutil.disk_usage') as disk:
        disk.return_value.free = 1
        task = begin(client, hackathon, user)
        assert client.post(task['admit_url']).status_code == 429
        assert client.get(task['status_url']).json()['receipt']
    assert client.post(task['admit_url']).status_code == 200


def test_zip_preview_root_only_and_owner_access(client, hackathon, project, user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('README.md', 'hello')
        archive.writestr('src/main.py', 'print(1)')
        archive.writestr('src/nested/config.json', '{}')
        archive.writestr('<img src=x onerror=alert(1)>.txt', 'safe display')
    result = complete(client, hackathon, user, buffer.getvalue(), kind='source', filename='project.zip')
    with patch('zipfile.ZipFile.open', side_effect=AssertionError('Must not decompress content')):
        response = client.get(result['preview_url'])
    assert response.status_code == 200
    rows = {row['name']: row for row in response.json()['entries']}
    assert rows['src']['directory'] and rows['src']['files'] == 2
    assert rows['README.md']['size'] == 5
    assert 'main.py' not in rows
    other = User.objects.create_user(email='preview-outsider@example.org')
    client.force_login(other)
    assert client.get(result['preview_url']).status_code == 404
    assert client.get(endpoint(hackathon, 'project-status')).status_code == 404


def test_zip_preview_limits_results(client, hackathon, project, user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for index in range(210):
            archive.writestr(f'file-{index}.txt', 'hello')
    result = complete(client, hackathon, user, buffer.getvalue(), kind='source', filename='project.zip')
    data = client.get(result['preview_url']).json()
    assert data['total'] == 210 and data['truncated'] and len(data['entries']) == 200


def test_cancel_queue_preserves_upload_and_receipt(client, hackathon, project, user):
    task = begin(client, hackathon, user, png().read(), kind='poster', filename='poster.png')
    queued = queue_project(client, hackathon, project)
    response = client.post(endpoint(hackathon, 'cancel-submission-queue'))
    assert response.status_code == 200 and response.json()['state'] == 'changes'
    assert response.json()['receipt_id'] == queued['receipt_id']
    assert UploadSession.objects.get(pk=task['id']).state == 'uploading'


def test_cleanup_preserves_old_receipts(client, hackathon, project, user):
    result = complete(client, hackathon, user)
    UploadSession.objects.filter(pk=result['id']).update(updated=timezone.now() - timedelta(days=10))
    call_command('cleanup_hackathon_uploads')
    assert UploadSession.objects.filter(pk=result['id']).exists()


@pytest.mark.usefixtures('transactional_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_concurrent_admission_does_not_overbook(hackathon, project, user):
    first = uploads.start_upload(hackathon, user, project, payload())
    second = uploads.start_upload(hackathon, user, project, payload(kind='source'))
    barrier = Barrier(2)

    def admit(session):
        close_old_connections()
        try:
            with scope(event=hackathon.event):
                barrier.wait(timeout=10)
                try:
                    uploads.admit_upload(session)
                except uploads.UploadQueued:
                    return 'queued'
                return 'admitted'
        finally:
            close_old_connections()

    with patch('eventyay.hackathon.uploads.UPLOAD_SLOTS', 1), ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(admit, [first, second])) == ['admitted', 'queued']
    assert UploadSession.objects.filter(lease_expires_at__gt=timezone.now()).count() == 1


def test_failed_checksum_visible_in_status(client, hackathon, project, user):
    task = begin(client, hackathon, user, checksum='a' * 64)
    assert send_chunk(client, task, PDF).status_code == 200
    assert client.post(task['finish_url']).status_code == 409
    status = client.get(task['status_url']).json()
    assert '校验未通过' in status['last_error']
    assert status['state'] != 'complete'
    assert not Asset.objects.filter(project=project).exists()
