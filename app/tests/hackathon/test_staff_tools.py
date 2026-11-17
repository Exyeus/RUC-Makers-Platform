import hashlib
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.base import ContentFile

from eventyay.base.models import User
from eventyay.hackathon.models import Asset, PrivateStorage, Project, StaffAssignment
from eventyay.hackathon.staff_tools import provision_judges
from tests.hackathon.test_workflows import endpoint, project_data


pytestmark = pytest.mark.django_db


def test_batch_permissions_partial_failure_and_idempotency(client, hackathon, organizer_user, user):
    address = endpoint(hackathon, 'judge-batch')
    client.force_login(user)
    assert client.post(address, {'rows': '新人,new@example.org'}).status_code == 403
    assert not User.objects.filter(email='new@example.org').exists()
    client.force_login(organizer_user)
    response = client.post(
        address, {'rows': '新人,new@example.org\n无效,broken\n重复,new@example.org\n管理员,organizer@example.org'}
    )
    assert response.status_code == 200
    new = User.objects.get(email='new@example.org')
    assert not new.is_staff and not new.is_administrator
    password = new.password
    assert StaffAssignment.objects.filter(hackathon=hackathon, user=new, role='judge').count() == 1
    assert '需要姓名' not in response.content.decode()
    assert '已是本届评委' in response.content.decode()
    assert '账户已停用或具有管理权限' in response.content.decode()
    assert 'no-store' in response['Cache-Control']
    client.post(address, {'rows': '新人,new@example.org'})
    new.refresh_from_db()
    assert new.password == password
    assert StaffAssignment.objects.filter(user=organizer_user).count() == 0


def test_batch_does_not_reset_existing_or_cross_event_role(hackathon, organizer_user, user):
    password = user.password
    result = provision_judges(hackathon, organizer_user, [[user.fullname, user.email]])
    assert result[0]['password'] == ''
    user.refresh_from_db()
    assert user.password == password
    hackathon.results_published = True
    hackathon.save(update_fields=['results_published'])
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        provision_judges(hackathon, organizer_user, [['迟到', 'late@example.org']])
    assert not User.objects.filter(email='late@example.org').exists()


@pytest.fixture
def movie(hackathon, team, settings, tmp_path, monkeypatch):
    settings.DATA_DIR = str(tmp_path)
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', PrivateStorage())
    values = project_data()
    values.pop('version')
    project = Project.objects.create(hackathon=hackathon, team=team, **values)
    data = b'\x00\x00\x00\x18ftypmp42' + b'private-video-content' * 100
    asset = Asset(
        hackathon=hackathon,
        project=project,
        kind='video',
        filename='movie.mp4',
        original_filename='movie.mp4',
        size=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
    )
    asset.file.save('movie.mp4', ContentFile(data))
    return asset, data


def test_private_media_range_and_unrelated_denied(client, hackathon, user, movie):
    asset, data = movie
    media = endpoint(hackathon, 'media', asset_id=asset.pk)
    source = endpoint(hackathon, 'media-source', asset_id=asset.pk)
    client.force_login(user)
    response = client.get(source)
    assert response.status_code == 200
    assert response.json()['transport'] == 'relay'
    response = client.get(media, HTTP_RANGE='bytes=12-31')
    assert response.status_code == 206
    assert response['Content-Disposition'].startswith('inline')
    assert b''.join(response.streaming_content) == data[12:32]
    other = User.objects.create_user(email='stranger@example.org')
    client.force_login(other)
    assert client.get(media).status_code == 404
    assert client.get(source).status_code == 404
    client.force_login(user)
    original = hackathon.slug
    hackathon.slug = 'different'
    hackathon.save(update_fields=['slug'])
    assert client.get(source).status_code == 404
    hackathon.slug = original
    hackathon.save(update_fields=['slug'])


def test_direct_source_expiry_and_reauthorization(client, hackathon, user, movie):
    asset, data = movie
    client.force_login(user)
    from eventyay.hackathon.storage import S3Storage

    backend = MagicMock(spec=S3Storage)
    backend.bucket = 'private-bucket'
    backend.head.return_value = {'ContentType': 'video/mp4'}
    backend.key.return_value = 'private/key.mp4'
    backend.client.generate_presigned_url.return_value = 'https://oss.invalid/short-lived'
    with patch.object(asset.file.storage, 'backend', backend):
        response = client.get(endpoint(hackathon, 'media-source', asset_id=asset.pk))
        assert response.status_code == 200
        assert response.json()['transport'] == 'direct'
        assert backend.client.generate_presigned_url.call_args.kwargs['ExpiresIn'] == 300
        assert 'ResponseContentType' not in backend.client.generate_presigned_url.call_args.kwargs['Params']
        assert 'no-store' in response['Cache-Control']
    backend.head.return_value = {'ContentType': 'application/octet-stream'}
    backend.client.generate_presigned_url.reset_mock()
    with patch.object(asset.file.storage, 'backend', backend):
        response = client.get(endpoint(hackathon, 'media-source', asset_id=asset.pk))
        assert response.json()['transport'] == 'relay'
        assert response.json()['url'] == endpoint(hackathon, 'media', asset_id=asset.pk)
        backend.client.generate_presigned_url.assert_not_called()
    client.logout()
    assert client.get(endpoint(hackathon, 'media-source', asset_id=asset.pk)).status_code == 302


def test_published_award_cannot_change_with_same_state(client, hackathon, team, organizer_user):
    from eventyay.hackathon.forms import ReviewForm
    from tests.hackathon.test_workflows import approved_project

    project = approved_project(hackathon, team)
    hackathon.results_published = True
    hackathon.save(update_fields=['results_published'])
    initial = ReviewForm(instance=project).initial
    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'review', project_id=project.pk),
        {
            'state': project.state,
            'award': '擅自改奖',
            'equipment_checked': 'on',
            'review_note': '',
            'version': initial['version'],
            'review_token': initial['review_token'],
        },
    )
    assert response.status_code == 200
    assert '奖项已锁定' in response.content.decode()
    project.refresh_from_db()
    assert project.award == ''
