import hashlib
import io
import os
import zipfile
from datetime import timedelta
from uuid import uuid4

import pytest
import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon.forms import AssetForm
from eventyay.hackathon.models import Asset
from eventyay.hackathon.storage import PrivateStorage, S3Storage
from eventyay.hackathon.uploads import save_direct_asset
from tests.hackathon.test_accounts_uploads import PDF, begin, send_chunk
from tests.hackathon.test_accounts_uploads import project as project
from tests.hackathon.test_workflows import endpoint


@pytest.fixture(params=['local', 's3'])
def storage_backend(request, settings, tmp_path, monkeypatch):
    settings.DATA_DIR = str(tmp_path)
    settings.HACKATHON_STORAGE_BACKEND = request.param
    if request.param == 's3':
        endpoint = os.environ.get('HACKATHON_TEST_S3_ENDPOINT')
        if not endpoint:
            pytest.skip('Set HACKATHON_TEST_S3_ENDPOINT for isolated S3 integration.')
        settings.HACKATHON_S3 = {
            'endpoint': endpoint,
            'region': os.environ.get('HACKATHON_TEST_S3_REGION', 'cn-beijing'),
            'bucket': os.environ['HACKATHON_TEST_S3_BUCKET'],
            'prefix': f'integration-tests/{uuid4().hex}',
            'access_key': '',
            'secret_key': '',
            'credential_file': os.environ['HACKATHON_TEST_S3_CREDENTIAL_FILE'],
            'signature_version': os.environ.get('HACKATHON_TEST_S3_SIGNATURE_VERSION', 's3'),
            'addressing_style': 'virtual',
        }
    storage = PrivateStorage()
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', storage)
    yield storage
    for name, _ in storage.iter_objects():
        storage.delete(name)


@pytest.mark.django_db
def test_upload_replace_access_range_and_gc(
    client,
    hackathon,
    project,
    user,
    participant,
    organizer_user,
    storage_backend,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    initial = begin(client, hackathon, user)
    assert send_chunk(client, initial, PDF).status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(initial['finish_url'])
        assert response.status_code == 200, response.content
    result = response.json()
    asset = Asset.objects.get(project=project)
    old_name = asset.file.name
    with PrivateStorage().open(old_name, 'rb') as downloaded:
        assert hashlib.file_digest(downloaded, 'sha256').hexdigest() == asset.checksum
    # Duplicate finish is idempotent; download stays behind existing business authorization.
    response = client.get(result['download_url'], HTTP_RANGE='bytes=1-9')
    assert response.status_code == 206
    assert b''.join(response.streaming_content) == PDF[1:10]
    # Django test client closes a fully consumed streaming response automatically.
    response = client.head(result['download_url'])
    assert response.status_code == 200
    assert int(response['Content-Length']) == len(PDF)
    client.logout()
    assert client.get(result['download_url']).status_code in (302, 403, 404)
    # Storage outage must keep the existing reference and resumable part intact.
    task = begin(client, hackathon, user, PDF + b'new')
    assert send_chunk(client, task, PDF + b'new').status_code == 200
    with monkeypatch.context() as patch:
        if isinstance(storage_backend.backend, S3Storage):
            patch.setattr(S3Storage, '_save', lambda *args: (_ for _ in ()).throw(OSError('unavailable')))
        else:
            patch.setattr('eventyay.hackathon.uploads.os.link', lambda *args: (_ for _ in ()).throw(OSError('full')))
        assert client.post(task['finish_url']).status_code == 503
    asset.refresh_from_db()
    assert asset.file.name == old_name
    assert storage_backend.exists(old_name)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(task['finish_url']).status_code == 200
    assert client.post(task['finish_url']).status_code == 200
    asset.refresh_from_db()
    assert asset.file.name != old_name
    assert not storage_backend.exists(old_name)
    if not isinstance(storage_backend.backend, S3Storage):
        os.utime(asset.file.path, ((timezone.now() - timedelta(days=3)).timestamp(),) * 2)
    call_command('cleanup_hackathon_uploads')
    assert storage_backend.exists(asset.file.name)

    # Organizer intake shares the storage-safe direct upload path.
    previous = asset.file.name
    data = PDF + b'organizer intake'
    form = AssetForm({'kind': 'slides'}, {'file': SimpleUploadedFile('intake.pdf', data)})
    assert form.is_valid(), form.errors
    with django_capture_on_commit_callbacks(execute=True):
        replacement = save_direct_asset(
            hackathon, participant, project, form, organizer=organizer_user, reason='Test intake'
        )
    assert replacement.file.name != previous
    assert storage_backend.exists(replacement.file.name)
    assert not storage_backend.exists(previous)


@pytest.mark.django_db
def test_multiple_chunks_resume_and_full_download(
    client,
    hackathon,
    project,
    user,
    storage_backend,
    django_capture_on_commit_callbacks,
):
    data = PDF + os.urandom(9 * 1024 * 1024)
    first = data[: 8 * 1024 * 1024]
    task = begin(client, hackathon, user, data)
    assert send_chunk(client, task, first).status_code == 200
    # Query the receipt, then continue from the persisted offset.
    status = client.get(task['status_url'])
    assert status.status_code == 200
    assert status.json()['offset'] == len(first)
    assert send_chunk(client, task, data[len(first) :], offset=len(first)).status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(task['finish_url'])
    assert response.status_code == 200, response.content
    response = client.get(response.json()['download_url'])
    assert response.status_code == 200
    digest = hashlib.sha256()
    count = 0
    for block in response.streaming_content:
        digest.update(block)
        count += len(block)
    assert count == len(data)
    assert digest.hexdigest() == hashlib.sha256(data).hexdigest()


@pytest.mark.django_db
def test_zip_archive_and_storage_outages(
    client,
    hackathon,
    project,
    user,
    organizer_user,
    storage_backend,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('demo/README.txt', 'OSS integration acceptance')
    data = buffer.getvalue()
    task = begin(client, hackathon, user, data, kind='source', filename='demo.zip')
    assert send_chunk(client, task, data).status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(task['finish_url'])
    assert response.status_code == 200, response.content
    asset = Asset.objects.get(project=project)
    preview = endpoint(hackathon, 'asset-preview', asset_id=asset.pk)
    assert client.get(preview).json()['entries'][0]['name'] == 'demo'
    project.submitted_at = timezone.now()
    project.save(update_fields=['submitted_at'])
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'bundle'))
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    assert any(archive.read(name) == data for name in archive.namelist() if name.endswith('.zip'))

    def unavailable(*args, **kwargs):
        raise OSError('temporary storage outage')

    # Both metadata reads and completed direct uploads fail safely before a response starts.
    monkeypatch.setattr(PrivateStorage, '_open', unavailable)
    monkeypatch.setattr(PrivateStorage, '_save', unavailable)
    assert client.get(preview).status_code == 503
    assert client.get(endpoint(hackathon, 'bundle')).status_code == 503
    response = client.post(
        endpoint(hackathon, 'intake', team_id=project.team_id),
        {
            'action': 'upload',
            'kind': 'slides',
            'reason': 'Storage outage acceptance',
            'file': SimpleUploadedFile('outage.pdf', PDF),
        },
    )
    assert response.status_code == 503, response.content
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'upload'),
        {
            'kind': 'slides',
            'file': SimpleUploadedFile('outage.pdf', PDF),
        },
    )
    assert response.status_code == 503
    asset.refresh_from_db()
    assert storage_backend.exists(asset.file.name)


@pytest.mark.skipif(
    not os.environ.get('HACKATHON_TEST_LIVE_HTTP'),
    reason='Opt-in: requires the isolated live HTTP acceptance harness.',
)
@pytest.mark.django_db(transaction=True)
def test_real_http_upload_and_download(
    client,
    live_server,
    hackathon,
    project,
    user,
    storage_backend,
    settings,
):

    data = PDF + os.urandom(9 * 1024 * 1024)
    client.force_login(user)
    browser = requests.Session()
    browser.trust_env = False
    browser.headers['Host'] = 'testserver'
    browser.cookies.set(settings.SESSION_COOKIE_NAME, client.session.session_key)
    base = live_server.url
    page = browser.get(base + endpoint(hackathon, 'project'), timeout=15)
    assert page.status_code == 200
    browser.headers['X-CSRFToken'] = browser.cookies[settings.CSRF_COOKIE_NAME]
    payload = {
        'id': str(uuid4()),
        'kind': 'slides',
        'filename': 'OSS-HTTP-acceptance.pdf',
        'size': len(data),
        'checksum': hashlib.sha256(data).hexdigest(),
    }
    started = browser.post(base + endpoint(hackathon, 'upload-start'), data=payload, timeout=15)
    assert started.status_code == 200, started.text[:150]
    task = started.json()
    boundary = 8 * 1024 * 1024
    for offset in range(0, len(data), boundary):
        response = browser.post(
            base + task['chunk_url'],
            data=data[offset : offset + boundary],
            headers={'Content-Type': 'application/octet-stream', 'Upload-Offset': str(offset)},
            timeout=30,
        )
        assert response.status_code == 200, response.text[:150]
    response = browser.post(base + task['finish_url'], timeout=60)
    assert response.status_code == 200, response.text[:150]
    download = base + response.json()['download_url']
    response = browser.get(download, timeout=60)
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == payload['checksum']
    response = browser.get(download, headers={'Range': 'bytes=1-99'}, timeout=15)
    assert response.status_code == 206
    assert response.content == data[1:100]
    assert browser.head(download, timeout=15).headers['Content-Length'] == str(len(data))
    anonymous = requests.Session()
    anonymous.trust_env = False
    response = anonymous.get(download, headers={'Host': browser.headers['Host']}, allow_redirects=False, timeout=15)
    assert response.status_code in (302, 403, 404)
    outsider = User.objects.create_user(email='outside-storage-test@example.invalid', fullname='Other account')
    client.force_login(outsider)
    browser.cookies.set(settings.SESSION_COOKIE_NAME, client.session.session_key)
    assert browser.get(download, allow_redirects=False, timeout=15).status_code in (403, 404)
    browser.close()
    anonymous.close()
