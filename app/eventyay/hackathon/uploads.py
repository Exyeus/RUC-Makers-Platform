"""Durable sequential uploads. Database offsets acknowledge only fsynced bytes."""

import hashlib
import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from eventyay.hackathon.constants import CHUNK_SIZE, UPLOAD_LEASE_SECONDS, UPLOAD_LIFETIME_HOURS, UPLOAD_SLOTS
from eventyay.hackathon.forms import AssetForm
from eventyay.hackathon.models import Asset, DemoSlot, Hackathon, Participant, PrivateStorage, Project, UploadSession
from eventyay.hackathon.services import (
    audit,
    complete_queued_project,
    invalidate_queued_project,
    lock_event,
    require_project_editable,
    require_submission_open,
)


logger = logging.getLogger(__name__)


class UploadConflict(ValidationError):
    pass


class UploadQueued(ValidationError):
    pass


def part_path(session):
    return Path(PrivateStorage().location) / '.uploads' / f'{session.pk}.part'


def remove_file(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.exception('Could not remove hackathon temporary file %s', path)


def require_owner(hackathon, user, project):
    if not Participant.objects.filter(
        pk=project.team.captain_id, user=user, hackathon=hackathon, team=project.team
    ).exists():
        raise PermissionDenied


def active_uploads(project):
    return UploadSession.objects.filter(project=project, state='uploading', expires_at__gt=timezone.now())


def expire_uploads(hackathon):
    # Lock one upload at a time, in the same order as append/finalize/cancel.
    for pk in UploadSession.objects.filter(
        hackathon=hackathon, state='uploading', expires_at__lte=timezone.now()
    ).values_list('pk', flat=True):
        with transaction.atomic():
            session = UploadSession.objects.select_for_update(of=('self',)).get(pk=pk)
            if session.state == 'uploading' and session.expires_at <= timezone.now():
                session.state = UploadSession.State.CANCELLED
                session.save(update_fields=['state', 'updated'])
                invalidate_queued_project(session)
                transaction.on_commit(lambda path=part_path(session): remove_file(path))


def start_upload(hackathon, user, project, data):
    expire_uploads(hackathon)
    # An acknowledged intent can be resumed after the deadline with the same ID.
    existing = UploadSession.objects.filter(pk=data['id'], hackathon=hackathon, user=user).first()
    if existing:
        with transaction.atomic():
            existing = UploadSession.objects.select_for_update(of=('self',)).get(pk=existing.pk)
            require_owner(hackathon, user, project)
            if (
                existing.project_id != project.pk
                or existing.kind != data['kind']
                or existing.original_filename != data['filename']
                or existing.size != data['size']
                or (data['checksum'] and existing.expected_checksum and existing.expected_checksum != data['checksum'])
            ):
                raise UploadConflict('上传凭证对应的文件信息不一致，请使用原文件。')
            if data['checksum'] and not existing.expected_checksum:
                require_live(existing)
                existing.expected_checksum = data['checksum']
                existing.checksum_bound_at = timezone.now()
                existing.save(update_fields=['expected_checksum', 'checksum_bound_at', 'updated'])
            return existing
    with transaction.atomic():
        hackathon = lock_event(hackathon)
        require_submission_open(hackathon)
        project = Project.objects.select_for_update().select_related('team').get(pk=project.pk, hackathon=hackathon)
        require_owner(hackathon, user, project)
        require_project_editable(project)
        existing = UploadSession.objects.filter(pk=data['id']).first()
        if existing:
            if (
                existing.user_id != user.pk
                or existing.project_id != project.pk
                or existing.kind != data['kind']
                or existing.original_filename != data['filename']
                or existing.size != data['size']
                or existing.expected_checksum != data['checksum']
            ):
                raise UploadConflict('上传标识已被使用，请重新选择文件开始新的上传。')
            return existing
        active = active_uploads(project).filter(kind=data['kind']).first()
        if active:
            if (
                active.user_id == user.pk
                and data['checksum']
                and active.expected_checksum == data['checksum']
                and active.size == data['size']
            ):
                return active
            raise UploadConflict('此类材料已有上传任务。请继续该任务，或先取消它再替换文件。')
        now = timezone.now()
        return UploadSession.objects.create(
            id=data['id'],
            hackathon=hackathon,
            project=project,
            user=user,
            kind=data['kind'],
            original_filename=data['filename'],
            size=data['size'],
            expected_checksum=data['checksum'],
            expires_at=min(now + timedelta(hours=UPLOAD_LIFETIME_HOURS), hackathon.freeze_at),
            checksum_bound_at=now if data['checksum'] else None,
            client_clicked_at=data.get('client_clicked_at'),
            deadline_at=hackathon.submission_deadline,
        )


def require_live(session):
    if session.state != UploadSession.State.UPLOADING or session.expires_at <= timezone.now():
        raise UploadConflict('上传任务已完成、取消或过期，请刷新页面。')
    hackathon = Hackathon.objects.get(pk=session.hackathon_id)
    if timezone.now() >= hackathon.freeze_at or hackathon.results_published:
        raise ValidationError('材料已经最终冻结，不能继续上传。')
    if not session.deadline_at or session.created >= session.deadline_at:
        require_submission_open(hackathon)
    require_owner(session.hackathon, session.user, session.project)


@transaction.atomic
def admit_upload(session):
    """Short DB-backed admission lease shared across worker processes."""
    session = UploadSession.objects.select_for_update(of=('self',)).get(pk=session.pk)
    require_live(session)
    if not session.expected_checksum:
        raise UploadConflict('请先完成本地文件校验并绑定哈希，再上传分块。')
    lock_event(session.hackathon)
    now = timezone.now()
    occupied = (
        UploadSession.objects.filter(
            hackathon=session.hackathon,
            state='uploading',
            expires_at__gt=now,
            lease_expires_at__gt=now,
        )
        .exclude(pk=session.pk)
        .count()
    )
    if occupied >= UPLOAD_SLOTS:
        raise UploadQueued('上传通道繁忙，已保留提交凭证，正在排队等待。请保持页面打开。')
    root = Path(PrivateStorage().location)
    root.mkdir(parents=True, exist_ok=True)
    reserved = sum(
        size - offset
        for size, offset in UploadSession.objects.filter(
            hackathon=session.hackathon,
            state='uploading',
            lease_expires_at__gt=now,
        )
        .exclude(pk=session.pk)
        .values_list('size', 'offset')
    )
    if shutil.disk_usage(root).free < session.size - session.offset + reserved + 256 * 1024 * 1024:
        raise UploadQueued('存储空间暂时不足，提交凭证已保留。请联系技术人员，系统会继续重试。')
    session.lease_expires_at = min(now + timedelta(seconds=UPLOAD_LEASE_SECONDS), session.expires_at)
    session.save(update_fields=['lease_expires_at'])
    return session


@transaction.atomic
def append_chunk(session, stream, offset, length):
    session = (
        UploadSession.objects.select_for_update(of=('self',))
        .select_related('project__team', 'hackathon', 'user')
        .get(pk=session.pk)
    )
    require_live(session)
    if not session.expected_checksum:
        raise UploadConflict('文件尚未绑定校验哈希。')
    if length <= 0 or length > CHUNK_SIZE or offset < 0 or offset + length > session.size:
        raise ValidationError('分块大小或偏移量无效。')
    if offset > session.offset or (offset < session.offset and offset + length > session.offset):
        raise UploadConflict('分块顺序不一致，请从服务器确认的位置继续。')
    path = part_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and session.offset:
        raise UploadConflict('临时文件已丢失，请取消任务后重新上传。')
    with path.open('r+b' if path.exists() else 'w+b') as target:
        if os.fstat(target.fileno()).st_size < session.offset:
            raise UploadConflict('临时文件不完整，请取消任务后重新上传。')
        # Recover from a killed worker after a write but before database commit.
        target.truncate(session.offset)
        target.seek(offset)
        replay = offset < session.offset
        remaining = length
        while remaining:
            block = stream.read(min(256 * 1024, remaining))
            if not block:
                raise ValidationError('本次分块传输中断，请重试。')
            if replay:
                if target.read(len(block)) != block:
                    raise UploadConflict('重复分块内容不一致，请使用原文件续传，或取消任务重新上传。')
            else:
                target.write(block)
            remaining -= len(block)
        if not replay:
            target.flush()
            os.fsync(target.fileno())
            session.offset += length
        session.last_error = ''
        session.save(update_fields=['offset', 'last_error', 'updated'])
    return session


def asset_filename(project, kind, checksum, original):
    return f'{project.team.code}_{project.code}_{kind}_{checksum[:12]}{Path(original).suffix.lower()}'


def install_asset(hackathon, user, project, kind, original, size, checksum, stored_name):
    """Called with the event and project locked; filesystem work is already complete."""
    asset = Asset.objects.filter(project=project, kind=kind).first()
    if asset and asset.checksum == checksum and asset.size == size:
        return asset
    old_path = asset.file.path if asset and asset.file else None
    asset = asset or Asset(hackathon=hackathon, project=project, kind=kind)
    asset.file.name = stored_name
    asset.filename = asset_filename(project, kind, checksum, original)
    asset.original_filename = original
    asset.size, asset.checksum = size, checksum
    asset.save()
    if old_path and old_path != asset.file.path:
        transaction.on_commit(lambda: remove_file(old_path))
    project.revision += 1
    project.equipment_checked = False
    if project.state not in (Project.State.DRAFT, Project.State.QUEUED):
        project.state = Project.State.CHANGES
    project.save(update_fields=['revision', 'equipment_checked', 'state', 'updated'])
    DemoSlot.objects.filter(project=project, state='ready').update(state='waiting')
    audit(hackathon, user, '上传作品材料', project.code, f'{asset.get_kind_display()} · {checksum} · {original}')
    return asset


@transaction.atomic
def finish_upload(session):
    session = (
        UploadSession.objects.select_for_update(of=('self',))
        .select_related('project__team', 'hackathon', 'user')
        .get(pk=session.pk)
    )
    if session.state == UploadSession.State.COMPLETE:
        return session
    require_live(session)
    if session.offset != session.size:
        raise UploadConflict('文件尚未传完，请继续上传。')
    path = part_path(session)
    with path.open('rb') as source:
        upload = File(source, name=session.original_filename)
        form = AssetForm({'kind': session.kind}, {'file': upload})
        if not form.is_valid():
            raise ValidationError([message for errors in form.errors.values() for message in errors])
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != session.expected_checksum:
        raise UploadConflict('文件校验未通过，请取消任务后重新上传原文件。已有材料不受影响。')
    # Hashing and validation hold only this upload lock, never the event lock.
    hackathon = lock_event(session.hackathon)
    require_live(session)
    project = Project.objects.select_for_update().select_related('team').get(pk=session.project_id)
    require_owner(hackathon, session.user, project)
    require_project_editable(project, allow_queued=True)
    relative = (
        f'{hackathon.pk}/{project.team_id}/{project.pk}/{session.kind}/'
        f'{session.pk.hex}{Path(session.original_filename).suffix.lower()}'
    )
    destination = Path(PrivateStorage().path(relative))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The temporary and final directories share a filesystem. Hard-linking avoids
    # a second 512 MiB copy and keeps the resumable source until database commit.
    if not destination.exists():
        os.link(path, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    asset = install_asset(
        hackathon, session.user, project, session.kind, session.original_filename, session.size, digest, relative
    )
    if asset.file.name != relative:
        transaction.on_commit(lambda: remove_file(destination))
    session.state, session.asset, session.checksum = UploadSession.State.COMPLETE, asset, digest
    session.last_error = ''
    session.save(update_fields=['state', 'asset', 'checksum', 'last_error', 'updated'])
    complete_queued_project(project, session.user)
    transaction.on_commit(lambda: remove_file(path))
    return session


@transaction.atomic
def cancel_upload(session):
    session = UploadSession.objects.select_for_update(of=('self',)).get(pk=session.pk)
    if session.state == UploadSession.State.UPLOADING:
        session.state = UploadSession.State.CANCELLED
        session.save(update_fields=['state', 'updated'])
        invalidate_queued_project(session)
        transaction.on_commit(lambda: remove_file(part_path(session)))
    return session


def save_direct_asset(hackathon, participant, project, form, organizer=None, reason=''):
    """Compatibility for non-JS and organizer intake; never hash under event lock."""
    require_submission_open(hackathon, organizer, reason)
    require_project_editable(project)
    require_owner(hackathon, participant.user, project)
    kind, upload = form.cleaned_data['kind'], form.cleaned_data['file']
    if active_uploads(project).filter(kind=kind).exists():
        raise UploadConflict('同类材料正在分块上传，请先完成或取消任务。')
    digest = hashlib.sha256()
    for block in upload.chunks(1024 * 1024):
        digest.update(block)
    upload.seek(0)
    checksum = digest.hexdigest()
    candidate = Asset(hackathon=hackathon, project=project, kind=kind)
    candidate.file.save(upload.name, upload, save=False)
    installed = False
    try:
        with transaction.atomic():
            hackathon = lock_event(hackathon)
            require_submission_open(hackathon, organizer, reason)
            project = Project.objects.select_for_update().select_related('team').get(pk=project.pk, hackathon=hackathon)
            require_owner(hackathon, participant.user, project)
            require_project_editable(project)
            if active_uploads(project).filter(kind=kind).exists():
                raise UploadConflict('同类材料正在分块上传，请先完成或取消任务。')
            asset = install_asset(
                hackathon,
                organizer or participant.user,
                project,
                kind,
                upload.name,
                upload.size,
                checksum,
                candidate.file.name,
            )
            installed = asset.file.name == candidate.file.name
            if organizer:
                audit(hackathon, organizer, '主办方材料补录', project.code, reason)
        return asset
    finally:
        if not installed:
            remove_file(candidate.file.path)
