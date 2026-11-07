import os
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from eventyay.hackathon.forms import AssetForm
from eventyay.hackathon.models import Asset
from eventyay.hackathon.storage import PrivateStorage, S3Storage
from eventyay.hackathon.uploads import save_direct_asset
from tests.hackathon.test_accounts_uploads import PDF, begin, complete, send_chunk
from tests.hackathon.test_accounts_uploads import project as project


@pytest.fixture(params=['local', 's3'])
def storage_backend(request, settings, tmp_path, monkeypatch):
    settings.DATA_DIR = str(tmp_path)
    settings.HACKATHON_STORAGE_BACKEND = request.param
    if request.param == 's3':
        endpoint = os.environ.get('HACKATHON_TEST_S3_ENDPOINT')
        if not endpoint:
            pytest.skip('Set HACKATHON_TEST_S3_ENDPOINT for isolated S3 integration.')
        settings.HACKATHON_S3 = {
            'endpoint': endpoint, 'region': 'us-east-1', 'bucket': 'ruc-storage-test',
            'prefix': f'test-{uuid4().hex}', 'access_key': 'test-access', 'secret_key': 'test-secret-key',
            'addressing_style': 'path',
        }
    storage = PrivateStorage()
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', storage)
    yield storage
    for name, _ in storage.iter_objects():
        storage.delete(name)


@pytest.mark.django_db(transaction=True)
def test_upload_replace_access_range_and_gc(
    client, hackathon, project, user, participant, organizer_user, storage_backend, monkeypatch
):
    result = complete(client, hackathon, user)
    asset = Asset.objects.get(project=project)
    old_name = asset.file.name
    # Duplicate finish is idempotent; download stays behind existing business authorization.
    response = client.get(result['download_url'], HTTP_RANGE='bytes=1-9')
    assert response.status_code == 206
    assert b''.join(response.streaming_content) == PDF[1:10]
    response.close()
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
    replacement = save_direct_asset(
        hackathon, participant, project, form, organizer=organizer_user, reason='Test intake'
    )
    assert replacement.file.name != previous
    assert storage_backend.exists(replacement.file.name)
    assert not storage_backend.exists(previous)
