import json
import time
from unittest.mock import Mock

import pytest

from eventyay.hackathon import storage


pytest.importorskip('boto3', reason='Install deployment/requirements-s3.txt to test the optional S3 backend.')


@pytest.fixture
def credential_backend(settings, tmp_path, monkeypatch):
    path = tmp_path / 'oss-sts.json'
    values = {
        'access_key_id': 'temporary-key',
        'access_key_secret': 'temporary-secret',
        'session_token': 'temporary-token',
        'expires_at': int(time.time()) + 600,
    }
    path.write_text(json.dumps(values))
    path.chmod(0o600)
    settings.HACKATHON_S3 = {
        'bucket': 'isolated-test-bucket',
        'prefix': 'isolated-test',
        'endpoint': 'https://oss-cn-beijing.aliyuncs.com',
        'region': 'cn-beijing',
        'credential_file': str(path),
        'signature_version': 's3v4',
        'addressing_style': 'virtual',
    }
    client = Mock(side_effect=[Mock(), Mock()])
    monkeypatch.setattr(storage.boto3, 'client', client)
    return storage.S3Storage(), path, values, client


def test_reloads_rotated_sts_file_without_restart(credential_backend):
    backend, path, values, factory = credential_backend
    first = backend.client
    assert backend.client is first
    values['session_token'] = 'rotated-token'
    path.write_text(json.dumps(values))
    assert backend.client is not first
    assert factory.call_count == 2
    assert factory.call_args.kwargs['aws_session_token'] == 'rotated-token'


@pytest.mark.parametrize('expiry', [0, True, float('inf'), float('nan'), 'tomorrow'])
def test_expired_or_invalid_sts_fails_closed(credential_backend, expiry):
    backend, path, values, factory = credential_backend
    backend.client
    values['expires_at'] = expiry
    path.write_text(json.dumps(values))
    with pytest.raises(OSError, match='expired'):
        backend.client
    assert factory.call_count == 1


def test_rejects_shared_or_symlinked_credential_file(credential_backend, tmp_path):
    backend, path, values, factory = credential_backend
    path.chmod(0o644)
    with pytest.raises(OSError, match='0600'):
        backend.client
    path.chmod(0o600)
    link = tmp_path / 'linked-sts.json'
    link.symlink_to(path)
    backend.conf['credential_file'] = str(link)
    with pytest.raises(OSError):
        backend.client
    factory.assert_not_called()
