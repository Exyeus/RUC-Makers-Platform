import hashlib
import io
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from eventyay.hackathon import direct_uploads
from eventyay.hackathon.constants import CHUNK_SIZE
from eventyay.hackathon.models import Asset, UploadSession
from eventyay.hackathon.storage import PrivateStorage
from tests.hackathon.test_accounts_uploads import PDF, begin
from tests.hackathon.test_accounts_uploads import project as project


pytestmark = pytest.mark.django_db


class Cloud:
    """An independent cloud view: acknowledgements must come from this service."""
    bucket = 'test-private'

    def __init__(self):
        self.client = self
        self.objects, self.uploads = {}, {}
        self.aborted, self.signed = [], []

    def key(self, name):
        return 'private/' + name

    def create_multipart_upload(self, **kwargs):
        identifier = uuid4().hex
        self.uploads[identifier] = {}
        return {'UploadId': identifier}

    def generate_presigned_url(self, operation, Params, **kwargs):
        self.signed.append((operation, Params, kwargs))
        return 'https://cloud.invalid/part/' + str(Params['PartNumber'])

    def list_parts(self, UploadId, **kwargs):
        return {'Parts': [{'PartNumber': n, 'Size': len(value), 'ETag': str(n)}
                          for n, value in sorted(self.uploads[UploadId].items())]}

    def complete_multipart_upload(self, UploadId, Key, MultipartUpload, **kwargs):
        self.objects[Key] = b''.join(self.uploads[UploadId][p['PartNumber']] for p in MultipartUpload['Parts'])
        del self.uploads[UploadId]

    def abort_multipart_upload(self, UploadId, **kwargs):
        self.aborted.append(UploadId)
        self.uploads.pop(UploadId, None)

    def exists(self, name):
        return self.key(name) in self.objects

    def size(self, name):
        return len(self.objects[self.key(name)])

    def get_object(self, Key, **kwargs):
        value = self.objects[Key]
        body = io.BytesIO(value)
        body.iter_chunks = lambda size: iter(lambda: body.read(size), b'')
        return {'Body': body}

    def delete(self, name):
        self.objects.pop(self.key(name), None)


@pytest.fixture
def cloud(settings, monkeypatch, tmp_path):
    settings.HACKATHON_STORAGE_BACKEND = 's3'
    settings.HACKATHON_DIRECT_UPLOADS = True
    settings.DATA_DIR = str(tmp_path)
    fake = Cloud()
    monkeypatch.setattr(PrivateStorage, 'backend', property(lambda self: fake))
    # Keep the real type guard, backed by a service double that has independent state.
    monkeypatch.setattr(direct_uploads, 'S3Storage', Cloud)
    return fake


def prepare(client, task):
    response = client.post(task['direct_url'])
    assert response.status_code == 200, response.content
    return response.json()


def put(cloud, task, number, data):
    session = UploadSession.objects.get(pk=task['id'])
    cloud.uploads[session.storage_upload_id][number] = data


def test_direct_multipart_resume_verify_and_idempotence(client, hackathon, project, user, cloud):
    data = PDF + b'\n' * CHUNK_SIZE
    task = begin(client, hackathon, user, data, transport='direct')
    assert task['transport'] == 'direct'
    initial = prepare(client, task)
    assert len(initial['parts']) == 2
    assert all('private/' in item[1]['Key'] and item[2]['ExpiresIn'] <= 600 for item in cloud.signed)
    response = client.post(task['chunk_url'], b'x', content_type='application/octet-stream', HTTP_UPLOAD_OFFSET='0')
    assert response.status_code == 409
    assert client.post(task['ack_url'], {'offset': 0}).status_code == 409
    put(cloud, task, 1, data[:CHUNK_SIZE - 1])
    assert client.post(task['ack_url'], {'offset': 0}).status_code == 409
    put(cloud, task, 1, data[:CHUNK_SIZE])
    response = client.post(task['ack_url'], {'offset': 0})
    assert response.json()['offset'] == CHUNK_SIZE
    assert client.post(task['ack_url'], {'offset': 0}).json()['offset'] == CHUNK_SIZE
    assert client.post(task['finish_url']).status_code == 409
    resumed = begin(client, hackathon, user, data, id=task['id'])
    assert resumed['transport'] == 'direct' and resumed['offset'] == CHUNK_SIZE
    assert prepare(client, resumed)['parts'][0]['offset'] == CHUNK_SIZE
    put(cloud, task, 2, data[CHUNK_SIZE:])
    assert client.post(task['ack_url'], {'offset': CHUNK_SIZE}).json()['offset'] == len(data)
    response = client.post(task['finish_url'])
    assert response.status_code == 200, response.content
    asset = Asset.objects.get(project=project)
    assert asset.checksum == hashlib.sha256(data).hexdigest()
    assert cloud.objects[cloud.key(asset.file.name)] == data
    assert client.post(task['finish_url']).json()['state'] == 'complete'


def test_direct_rejects_corruption_before_installing(client, hackathon, project, user, cloud):
    task = begin(client, hackathon, user, PDF, transport='direct')
    prepare(client, task)
    put(cloud, task, 1, PDF[:-1] + b'!')
    assert client.post(task['ack_url'], {'offset': 0}).status_code == 200
    response = client.post(task['finish_url'])
    assert response.status_code == 409
    assert not Asset.objects.filter(project=project).exists()
    assert UploadSession.objects.get(pk=task['id']).state == 'uploading'


def test_direct_requires_bound_hash_and_owner(client, hackathon, project, user, organizer_user, cloud):
    task = begin(client, hackathon, user, PDF, transport='direct', checksum='')
    assert client.post(task['direct_url']).status_code == 409
    bound = begin(client, hackathon, user, PDF, id=task['id'], transport='direct')
    prepare(client, bound)
    client.force_login(organizer_user)
    assert client.post(task['direct_url']).status_code in (403, 404)
    assert client.post(task['ack_url'], {'offset': 0}).status_code in (403, 404)


def test_direct_cancellation_cleans_only_its_cloud_objects(
    client, hackathon, project, user, cloud, django_capture_on_commit_callbacks
):
    task = begin(client, hackathon, user, PDF, transport='direct')
    prepare(client, task)
    put(cloud, task, 1, PDF)
    cloud.objects['private/unrelated'] = b'preserve'
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(task['cancel_url']).status_code == 200
    assert cloud.aborted and cloud.objects == {'private/unrelated': b'preserve'}
    assert UploadSession.objects.get(pk=task['id']).storage_name == ''


def test_direct_flag_does_not_convert_existing_relay(client, hackathon, project, user, settings, cloud):
    settings.HACKATHON_DIRECT_UPLOADS = False
    task = begin(client, hackathon, user, PDF, transport='direct')
    assert task['transport'] == 'relay'
    settings.HACKATHON_DIRECT_UPLOADS = True
    assert begin(client, hackathon, user, PDF, transport='direct', id=task['id'])['transport'] == 'relay'


def test_direct_deadline_resume_and_freeze(client, hackathon, project, user, cloud):
    task = begin(client, hackathon, user, PDF, transport='direct')
    prepare(client, task)
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save(update_fields=['submission_deadline'])
    put(cloud, task, 1, PDF)
    assert client.post(task['ack_url'], {'offset': 0}).status_code == 200
    hackathon.freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save(update_fields=['freeze_at'])
    assert client.post(task['finish_url']).status_code == 400
    assert not Asset.objects.filter(project=project).exists()


def test_old_workers_can_insert_without_new_columns(client, hackathon, project, user, cloud):
    task = begin(client, hackathon, user, PDF)
    old_columns = [field.column for field in UploadSession._meta.local_fields
                   if field.name not in {'id', 'state', 'transport', 'storage_name', 'storage_upload_id'}]
    quoted = ', '.join(connection.ops.quote_name(column) for column in old_columns)
    clone_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO hackathon_uploadsession (id, state, {quoted}) '
            f'SELECT %s, %s, {quoted} FROM hackathon_uploadsession WHERE id = %s',
            [clone_id, 'cancelled', task['id']],
        )
    clone = UploadSession.objects.get(pk=clone_id)
    assert clone.transport == 'relay' and clone.storage_name == '' and clone.storage_upload_id == ''
