"""Private, short-lived multipart URLs; only verified objects become materials."""

import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from eventyay.hackathon import uploads
from eventyay.hackathon.constants import CHUNK_SIZE
from eventyay.hackathon.forms import AssetForm
from eventyay.hackathon.models import UploadSession
from eventyay.hackathon.storage import STORAGE_ERRORS, PrivateStorage, S3Storage


logger = logging.getLogger(__name__)


def enabled():
    return getattr(settings, 'HACKATHON_DIRECT_UPLOADS', False) and settings.HACKATHON_STORAGE_BACKEND == 's3'


def backend(session):
    if session.transport != 'direct':
        raise uploads.UploadConflict('此任务使用兼容上传通道，请继续原任务。')
    storage = PrivateStorage().backend
    if not isinstance(storage, S3Storage):
        raise ValidationError('直传存储暂时不可用，请稍后继续原任务。')
    return storage


def parts(storage, session):
    result = storage.client.list_parts(
        Bucket=storage.bucket, Key=storage.key(session.storage_name), UploadId=session.storage_upload_id
    )
    if result.get('IsTruncated'):
        raise uploads.UploadConflict('云端分块数量异常，请联系技术人员。')
    return result.get('Parts', [])


def prepare(session):
    uploads.admit_upload(session)
    with transaction.atomic():
        session = UploadSession.objects.select_for_update(of=('self',)).get(pk=session.pk)
        uploads.require_live(session)
        storage = backend(session)
        if session.offset == session.size:
            return session, []
        if not session.storage_upload_id:
            if not session.storage_name:
                session.storage_name = (
                    f'{session.hackathon_id}/{session.project.team_id}/{session.project_id}/{session.kind}/'
                    f'direct-{uuid4().hex}{Path(session.original_filename).suffix.lower()}'
                )
            # This is a playback metadata hint; finish still validates and decodes the object before exposure.
            content_type = 'application/octet-stream'
            if session.kind in {'preliminary', 'video'}:
                content_type = {'.mp4': 'video/mp4', '.webm': 'video/webm'}.get(
                    Path(session.original_filename).suffix.lower(), 'application/octet-stream'
                )
            result = storage.client.create_multipart_upload(
                Bucket=storage.bucket, Key=storage.key(session.storage_name), ContentType=content_type
            )
            session.storage_upload_id = result['UploadId']
            session.save(update_fields=['storage_name', 'storage_upload_id', 'updated'])
        # Four URLs amortise application round trips; no secret key or bucket-wide grant leaves the server.
        lifetime = max(1, min(600, int((session.expires_at - timezone.now()).total_seconds())))
        urls = []
        for offset in range(session.offset, min(session.size, session.offset + 4 * CHUNK_SIZE), CHUNK_SIZE):
            length = min(CHUNK_SIZE, session.size - offset)
            url = storage.client.generate_presigned_url(
                'upload_part',
                Params={
                    'Bucket': storage.bucket,
                    'Key': storage.key(session.storage_name),
                    'UploadId': session.storage_upload_id,
                    'PartNumber': offset // CHUNK_SIZE + 1,
                    'ContentLength': length,
                },
                ExpiresIn=lifetime,
                HttpMethod='PUT',
            )
            urls.append({'offset': offset, 'size': length, 'url': url})
        return session, urls


def acknowledge(session, offset):
    uploads.admit_upload(session)
    with transaction.atomic():
        session = UploadSession.objects.select_for_update(of=('self',)).get(pk=session.pk)
        uploads.require_live(session)
        storage = backend(session)
        if offset < 0 or offset % CHUNK_SIZE or offset >= session.size or offset > session.offset:
            raise uploads.UploadConflict('云端分块位置不一致，请查询任务后继续。')
        if offset < session.offset:
            return session  # Lost acknowledgement: replay is idempotent.
        number = offset // CHUNK_SIZE + 1
        part = next((part for part in parts(storage, session) if part['PartNumber'] == number), None)
        if part is None or part['Size'] != min(CHUNK_SIZE, session.size - offset):
            raise uploads.UploadConflict('云端尚未确认完整分块，请重新传输此分块。')
        session.offset += part['Size']
        session.last_error = ''
        session.save(update_fields=['offset', 'last_error', 'updated'])
        return session


def verify(session):
    """Called under the session lock. Never trust a client-reported size, ETag or hash."""
    storage = backend(session)
    if not session.storage_name or not session.storage_upload_id:
        raise uploads.UploadConflict('尚未创建云端上传任务，请重新选择原文件继续。')
    # A previous finish may have completed OSS but lost its database transaction.
    if not storage.exists(session.storage_name):
        remote = parts(storage, session)
        expected = list(range(1, (session.size + CHUNK_SIZE - 1) // CHUNK_SIZE + 1))
        if [part['PartNumber'] for part in remote] != expected or any(
            part['Size'] != min(CHUNK_SIZE, session.size - (part['PartNumber'] - 1) * CHUNK_SIZE)
            for part in remote
        ):
            raise uploads.UploadConflict('云端文件尚未完整传输，请重新选择原文件继续。')
        storage.client.complete_multipart_upload(
            Bucket=storage.bucket,
            Key=storage.key(session.storage_name),
            UploadId=session.storage_upload_id,
            MultipartUpload={'Parts': [{'PartNumber': p['PartNumber'], 'ETag': p['ETag']} for p in remote]},
        )
    if storage.size(session.storage_name) != session.size:
        raise uploads.UploadConflict('云端文件大小不一致，已有材料不受影响。')
    path = uploads.part_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f'{path.name}.{uuid4().hex}.verify')
    response = storage.client.get_object(Bucket=storage.bucket, Key=storage.key(session.storage_name))
    body = response['Body']
    digest = hashlib.sha256()
    count = 0
    try:
        with candidate.open('wb') as target:
            for block in body.iter_chunks(256 * 1024):
                count += len(block)
                if count > session.size:
                    raise uploads.UploadConflict('云端文件大小不一致。')
                target.write(block)
                digest.update(block)
            target.flush()
            os.fsync(target.fileno())
        if count != session.size or digest.hexdigest() != session.expected_checksum:
            raise uploads.UploadConflict('文件校验未通过，请取消后重新上传原文件。已有材料不受影响。')
        with candidate.open('rb') as source:
            form = AssetForm(
                {'kind': session.kind},
                {'file': File(source, name=session.original_filename)},
                hackathon=session.hackathon,
            )
            if not form.is_valid():
                raise ValidationError([message for errors in form.errors.values() for message in errors])
            session._media_probe = form.cleaned_data.get('media_probe', {})
        os.replace(candidate, path)
    finally:
        body.close()
        uploads.remove_file(candidate)
    return digest.hexdigest(), session.storage_name


def cleanup(session_id, name, upload_id):
    """Retryable cleanup for cancelled/expired tasks; active assets are never targeted."""
    session = UploadSession.objects.get(pk=session_id)
    if session.state != 'cancelled' or session.asset_id or not name:
        return
    try:
        storage = backend(session)
        if upload_id:
            try:
                storage.client.abort_multipart_upload(
                    Bucket=storage.bucket, Key=storage.key(name), UploadId=upload_id
                )
            except Exception as error:
                if getattr(error, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchUpload', '404'):
                    raise
        storage.delete(name)
        UploadSession.objects.filter(pk=session_id, state='cancelled').update(storage_upload_id='', storage_name='')
    except STORAGE_ERRORS:
        logger.exception('Could not clean up cancelled direct upload %s', session_id)


def cleanup_after_commit(session):
    if session.transport == 'direct' and session.storage_name:
        transaction.on_commit(lambda: cleanup(session.pk, session.storage_name, session.storage_upload_id))
