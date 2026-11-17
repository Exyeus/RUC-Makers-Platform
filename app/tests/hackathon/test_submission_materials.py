import hashlib
import io
import json
import os
import subprocess
import uuid
import zipfile
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from eventyay.hackathon import forms, services, uploads
from eventyay.hackathon.models import (
    Asset,
    JudgingRound,
    MaterialPolicy,
    PrivateStorage,
    Project,
    ResultSnapshot,
    Team,
    UploadSession,
)
from eventyay.hackathon.submission_materials import capture_intent, capture_snapshot, policy_submission_errors
from tests.hackathon.test_workflows import endpoint, project_data


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def material_storage(settings, tmp_path, monkeypatch):
    settings.DATA_DIR = str(tmp_path)
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', PrivateStorage())


@pytest.fixture
def project(hackathon, team):
    data = project_data()
    data.pop('version')
    return Project.objects.create(hackathon=hackathon, team=team, **data)


def asset(project, kind='poster', data=b'poster', **changes):
    item = Asset(
        hackathon=project.hackathon,
        project=project,
        kind=kind,
        filename=f'{kind}.pdf',
        original_filename=f'{kind}.pdf',
        checksum=hashlib.sha256(data).hexdigest(),
        size=len(data),
        **changes,
    )
    item.file.save(f'{kind}.pdf', SimpleUploadedFile(f'{kind}.pdf', data))
    return item


def video(tmp_path, seconds):
    path = tmp_path / f'clip-{seconds}.mp4'
    subprocess.run(
        [
            'ffmpeg',
            '-nostdin',
            '-v',
            'error',
            '-f',
            'lavfi',
            '-i',
            f'color=red:s=32x32:r=1:d={seconds}',
            '-c:v',
            'libx264',
            '-pix_fmt',
            'yuv420p',
            str(path),
        ],
        check=True,
    )
    return SimpleUploadedFile(path.name, path.read_bytes(), content_type='video/mp4')


def test_real_video_checks_silent_corrupt_and_boundaries(hackathon, tmp_path):
    MaterialPolicy.objects.create(hackathon=hackathon)
    silent = forms.AssetForm({'kind': 'preliminary'}, {'file': video(tmp_path, 2)}, hackathon=hackathon)
    assert silent.is_valid(), silent.errors
    assert silent.cleaned_data['media_probe']['has_audio'] is False
    assert silent.cleaned_data['media_probe']['duration_seconds'] == 2
    for duration, kind in [(61, 'preliminary'), (181, 'video')]:
        form = forms.AssetForm({'kind': kind}, {'file': video(tmp_path, duration)}, hackathon=hackathon)
        assert not form.is_valid()
        assert '超过' in str(form.errors)
    forged = forms.AssetForm(
        {'kind': 'preliminary'},
        {'file': SimpleUploadedFile('bad.mp4', b'0000ftyp' + b'fake' * 20)},
        hackathon=hackathon,
    )
    assert not forged.is_valid()
    assert '视频损坏' in str(forged.errors)


def test_info_gate_on_new_task_and_explicit_kind(hackathon, project, user, client):
    project.description = ''
    project.save()
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'upload-start'),
        {'id': uuid.uuid4(), 'kind': 'slides', 'filename': 'show.pdf', 'size': 8, 'checksum': ''},
    )
    assert response.status_code == 400
    assert '补齐' in response.json()['error']
    assert not UploadSession.objects.filter(project=project).exists()
    form = forms.AssetForm({'kind': ''}, {'file': SimpleUploadedFile('show.pdf', b'%PDF-1.7')}, hackathon=hackathon)
    assert not form.is_valid()
    assert 'kind' in form.errors


def test_preliminary_cannot_be_replaced_by_demo_or_final_video(hackathon, project):
    MaterialPolicy.objects.create(hackathon=hackathon)
    asset(project, 'poster')
    asset(project, 'video', media_probe={'state': 'verified', 'duration_seconds': 100})
    project.final_material_kind = 'video'
    project.save()
    assert any('独立的初评短视频' in message for message in policy_submission_errors(project))
    asset(project, 'preliminary', media_probe={'state': 'verified', 'duration_seconds': 20})
    assert policy_submission_errors(project) == []
    assert set(project.assets.values_list('kind', flat=True)) == {'poster', 'video', 'preliminary'}


def test_late_hash_and_transfer_registered_intent_can_submit_without_edit(
    hackathon, project, participant, user, organizer_user
):
    asset(project)
    task = UploadSession.objects.create(
        hackathon=hackathon,
        project=project,
        user=user,
        kind='slides',
        original_filename='show.pdf',
        size=8,
        expires_at=hackathon.freeze_at,
        deadline_at=hackathon.submission_deadline,
        expected_checksum='',
        intent_manifest=capture_intent(project),
    )
    after = hackathon.submission_deadline + timedelta(seconds=2)
    with patch('django.utils.timezone.now', return_value=after):
        resumed = uploads.start_upload(
            hackathon,
            user,
            project,
            {
                'id': task.pk,
                'kind': task.kind,
                'filename': task.original_filename,
                'size': task.size,
                'checksum': 'a' * 64,
            },
        )
        assert resumed.checksum_bound_at == after
        form = forms.ProjectForm(project_data(version=project.revision), instance=project)
        assert form.is_valid(), form.errors
        queued = services.save_project(hackathon, participant, form, submit=True)
        assert queued.state == Project.State.QUEUED
        snapshot = capture_snapshot(hackathon, organizer_user, '11:00 intent capture')
        assert snapshot.manifest['entries'][0]['eligibility'] == 'registered_intent'


def test_late_intent_cannot_change_content_or_register_new_task(hackathon, project, participant, user):
    UploadSession.objects.create(
        hackathon=hackathon,
        project=project,
        user=user,
        kind='slides',
        original_filename='show.pdf',
        size=8,
        expires_at=hackathon.freeze_at,
        deadline_at=hackathon.submission_deadline,
        intent_manifest=capture_intent(project),
    )
    after = hackathon.submission_deadline + timedelta(seconds=2)
    with patch('django.utils.timezone.now', return_value=after):
        form = forms.ProjectForm(project_data(title='late changed', version=project.revision), instance=project)
        assert form.is_valid()
        with pytest.raises(ValidationError, match='截止'):
            services.save_project(hackathon, participant, form, submit=True)
        with pytest.raises(ValidationError, match='截止'):
            uploads.start_upload(
                hackathon,
                user,
                project,
                {'id': uuid.uuid4(), 'kind': 'video', 'filename': 'late.mp4', 'size': 20, 'checksum': ''},
            )


def test_snapshot_fixed_hash_and_retained_file_on_replacement(
    hackathon, project, organizer_user, django_capture_on_commit_callbacks
):
    old = asset(project, 'slides', b'%PDF-old')
    project.final_material_kind = 'slides'
    project.state = Project.State.SUBMITTED
    project.save()
    snapshot = capture_snapshot(hackathon, organizer_user, 'fixed materials')
    old_name, old_manifest = old.file.name, json.dumps(snapshot.manifest, sort_keys=True)
    candidate = Asset(hackathon=hackathon, project=project, kind='slides')
    candidate.file.save('new.pdf', SimpleUploadedFile('new.pdf', b'%PDF-new'), save=False)
    with django_capture_on_commit_callbacks(execute=True):
        uploads.install_asset(
            hackathon,
            organizer_user,
            project,
            'slides',
            'new.pdf',
            8,
            hashlib.sha256(b'%PDF-new').hexdigest(),
            candidate.file.name,
        )
    snapshot.refresh_from_db()
    assert json.dumps(snapshot.manifest, sort_keys=True) == old_manifest
    assert PrivateStorage().exists(old_name)
    with pytest.raises(ValidationError, match='不可覆盖'):
        snapshot.save()


def test_bundle_uses_signed_material_version_not_current(hackathon, project, organizer_user, client):
    project.final_material_kind = 'slides'
    project.state = Project.State.SUBMITTED
    project.save()
    original = asset(project, 'slides', b'%PDF-frozen')
    frozen = capture_snapshot(hackathon, organizer_user, 'frozen')
    preliminary = JudgingRound.objects.create(
        hackathon=hackathon,
        kind='preliminary',
        version=1,
        name='initial',
        rules={},
        panel=[],
        tasks=[],
        signer=organizer_user,
        frozen_at=timezone.now(),
        frozen_by=organizer_user,
    )
    result = ResultSnapshot.objects.create(
        hackathon=hackathon,
        round=preliminary,
        kind='finalists',
        version=1,
        manifest={
            'material_snapshot_id': str(frozen.pk),
            'material_snapshot_sha256': frozen.sha256,
            'entries': [{'project_id': str(project.pk), 'status': 'finalist', 'position': 1}],
        },
        sha256='b' * 64,
        reason='signed roster',
        signed_by=organizer_user,
        signed_at=timezone.now(),
    )
    final_round = JudgingRound.objects.create(
        hackathon=hackathon,
        kind='final',
        version=1,
        name='final',
        rules={'material_snapshot_id': str(frozen.pk), 'material_snapshot_sha256': frozen.sha256},
        panel=[],
        tasks=[],
        signer=organizer_user,
        frozen_at=timezone.now(),
        frozen_by=organizer_user,
        source_snapshot=result,
    )
    original.filename = 'changed.pdf'
    original.checksum = 'c' * 64
    original.save()
    client.force_login(organizer_user)
    with patch('eventyay.hackathon.workflow_services.current_signed_snapshot', return_value=result):
        response = client.get(endpoint(hackathon, 'bundle'))
    assert response.status_code == 200
    package = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    file_name = next(name for name in package.namelist() if name.endswith('.pdf'))
    assert package.read(file_name) == b'%PDF-frozen'
    manifest = json.loads(package.read('manifest.json'))
    assert manifest['material_snapshot_sha256'] == frozen.sha256
    assert manifest['final_round_id'] == str(final_round.pk)
    assert manifest['files'][0]['sha256'] == hashlib.sha256(b'%PDF-frozen').hexdigest()


def test_policy_and_snapshot_are_organizer_only(hackathon, user, client, project, organizer_user):
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'material-policy')).status_code == 403
    snapshot = capture_snapshot(hackathon, organizer_user, 'synthetic capture')
    assert client.get(endpoint(hackathon, 'material-snapshot', snapshot_id=snapshot.pk)).status_code == 403


def test_gc_preserves_old_frozen_materials(hackathon, project, organizer_user):
    original = asset(project, 'slides', b'%PDF-frozen')
    capture_snapshot(hackathon, organizer_user, 'retained')
    name = original.file.name
    original.file.name = 'not-the-frozen-file.pdf'
    original.save()
    path = PrivateStorage().path(name)
    old = (timezone.now() - timedelta(hours=72)).timestamp()
    os.utime(path, (old, old))
    call_command('cleanup_hackathon_uploads', stdout=io.StringIO())
    assert PrivateStorage().exists(name)


def test_retained_schema_accepts_old_code_insert_column_sets(hackathon, project, team, user):
    other_team = Team.objects.create(
        hackathon=hackathon, captain=team.captain, track=team.track, name='legacy rollback'
    )
    original_asset = asset(project)
    task = UploadSession.objects.create(
        hackathon=hackathon,
        project=project,
        user=user,
        kind='slides',
        original_filename='old.pdf',
        size=8,
        expires_at=hackathon.freeze_at,
    )

    def clone_old_row(model, original, omitted, changes):
        fields = [field for field in model._meta.concrete_fields if field.name != omitted]
        columns = ', '.join(connection.ops.quote_name(field.column) for field in fields)
        selected, values = [], []
        for field in fields:
            if field.name in changes:
                selected.append('%s')
                values.append(changes[field.name])
            else:
                selected.append(connection.ops.quote_name(field.column))
        values.append(original.pk)
        table = connection.ops.quote_name(model._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {table} ({columns}) SELECT {", ".join(selected)} FROM {table} WHERE id = %s', values
            )

    project_id, asset_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    clone_old_row(Project, project, 'final_material_kind', {'id': project_id, 'team': other_team.pk})
    clone_old_row(Asset, original_asset, 'media_probe', {'id': asset_id, 'kind': 'source'})
    clone_old_row(UploadSession, task, 'intent_manifest', {'id': task_id, 'kind': 'video'})
    assert Project.objects.get(pk=project_id).final_material_kind == ''
    assert Asset.objects.get(pk=asset_id).media_probe == {}
    assert UploadSession.objects.get(pk=task_id).intent_manifest == {}
