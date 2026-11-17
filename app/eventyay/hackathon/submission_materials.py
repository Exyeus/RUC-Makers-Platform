"""Server-owned material checks; snapshots never silently follow later uploads."""

import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from eventyay.hackathon.models import Asset, MaterialPolicy, Project, SubmissionSnapshot, UploadSession


def material_policy(hackathon):
    if not hackathon:
        return None
    return MaterialPolicy.objects.filter(hackathon=hackathon).first()


def material_kind_label(kind, policy):
    """Name material by its enabled purpose without changing persisted kind values."""
    if kind == Asset.Kind.VIDEO and policy and policy.enabled:
        return '现场视频'
    return dict(Asset.Kind.choices).get(kind, kind)


def information_errors(project):
    if project is None:
        return ['请先填写并保存作品信息。']
    missing = []
    for name, label in [
        ('title', '作品名称'),
        ('summary', '一句话介绍'),
        ('description', '功能与使用说明'),
        ('repository_url', '代码仓库'),
        ('demo_instructions', '演示步骤与设备需求'),
    ]:
        if not str(getattr(project, name, '') or '').strip():
            missing.append(label)
    team = project.team
    if not team.name.strip() or not team.track_id:
        missing.append('队伍名称与赛道')
    for member in team.members.all():
        if not all(str(getattr(member, field, '') or '').strip() for field in ('name', 'school', 'contact')):
            missing.append(f'队员 {member.code} 的姓名、学校或联系方式')
    return [f'请先补齐并保存：{label}。' for label in missing]


def require_information(project):
    errors = information_errors(project)
    if errors:
        raise ValidationError(errors)


def inspect_upload(upload, kind, hackathon=None):
    policy = material_policy(hackathon)
    if kind != 'preliminary' and not (kind == 'video' and policy and policy.enabled):
        return {}
    upload.seek(0)
    suffix = Path(upload.name).suffix.lower()
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as target:
            for block in upload.chunks(1024 * 1024):
                target.write(block)
            target.flush()
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v',
                    'error',
                    '-show_entries',
                    'format=duration:stream=codec_type,codec_name,width,height,duration',
                    '-of',
                    'json',
                    target.name,
                ],
                capture_output=True,
                timeout=45,
                check=False,
            )
            if result.returncode:
                raise ValidationError('视频损坏或无法解析，请重新导出 MP4/WebM 后上传；已有材料保留。')
            try:
                info = json.loads(result.stdout)
                streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
                duration = float(info.get('format', {}).get('duration', 0))
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise ValidationError('无法读取视频时长，请重新导出后上传。') from error
            if not streams or not math.isfinite(duration) or duration <= 0:
                raise ValidationError('文件必须包含有效视频画面和可读取的正时长。')
            if any(not s.get('width') or not s.get('height') for s in streams):
                raise ValidationError('无法读取视频画面，请重新导出后上传。')
            maximum = (
                (policy.preliminary_max_seconds if policy and policy.enabled else 60) if kind == 'preliminary' else 180
            )
            if maximum is not None and duration > maximum:
                label = '初评视频' if kind == 'preliminary' else '现场视频'
                raise ValidationError(f'{label}为 {duration:.2f} 秒，超过本届已配置的 {maximum} 秒上限。')
            decoded = subprocess.run(
                ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', target.name, '-map', '0:v:0', '-f', 'null', '-'],
                capture_output=True,
                timeout=90,
                check=False,
            )
            if decoded.returncode:
                raise ValidationError('视频画面无法完整解码，请重新导出后上传；已有材料保留。')
            return {
                'state': 'verified',
                'duration_seconds': duration,
                'video_codecs': [s.get('codec_name', '') for s in streams],
                'has_audio': any(s.get('codec_type') == 'audio' for s in info.get('streams', [])),
                'policy_revision': policy.revision if policy else None,
                'duration_limit_seconds': maximum,
                'note': '服务器已解析视频流；请在目标设备确认实际播放、声音与字幕。',
            }
    except FileNotFoundError as error:
        raise ValidationError('服务器视频校验暂不可用，请联系主办方；文件尚未标为合格，已有材料保留。') from error
    except subprocess.TimeoutExpired as error:
        raise ValidationError('视频校验超时，请重新导出或联系主办方；已有材料保留。') from error
    finally:
        upload.seek(0)


def policy_submission_errors(project, pending_kinds=()):
    policy = material_policy(project.hackathon)
    if not policy or not policy.enabled:
        return []
    assets = {asset.kind: asset for asset in project.assets.all()} if project.created else {}
    kinds = set(assets) | set(pending_kinds)
    errors = information_errors(project)
    if policy.require_preliminary_video:
        if 'preliminary' not in kinds:
            errors.append('请上传独立的初评短视频；Demo 或备用链接不能替代。')
        elif 'preliminary' in assets and 'preliminary' not in pending_kinds:
            probe = assets['preliminary'].media_probe
            if probe.get('state') != 'verified':
                errors.append('初评视频尚未通过服务器视频校验，请重新上传。')
            elif (
                policy.preliminary_max_seconds is not None
                and probe.get('duration_seconds', float('inf')) > policy.preliminary_max_seconds
            ):
                errors.append('初评视频超过当前配置的时长上限，请重新上传。')
    if policy.require_final_material:
        chosen = project.final_material_kind
        if chosen not in ('slides', 'video'):
            errors.append('请手动选择现场上场使用 PDF 演示文稿或现场视频，并保存。')
        elif chosen not in kinds:
            errors.append('请上传已选择的现场上场材料。')
        elif chosen == 'slides' and chosen in assets and not assets[chosen].filename.lower().endswith('.pdf'):
            errors.append('现场上场文稿必须是 PDF，请重新导出后上传。')
        elif chosen == 'video' and chosen in assets and chosen not in pending_kinds:
            probe = assets[chosen].media_probe
            if probe.get('state') != 'verified' or probe.get('duration_seconds', float('inf')) > 180:
                errors.append('现场视频必须通过服务器校验且不超过 3 分钟，请重新上传。')
    return errors


def asset_manifest(asset):
    return {
        'id': str(asset.pk),
        'kind': asset.kind,
        'filename': asset.original_filename or asset.filename,
        'storage_name': asset.file.name,
        'size': asset.size,
        'sha256': asset.checksum,
        'media_probe': asset.media_probe,
    }


@transaction.atomic
def capture_snapshot(hackathon, user, reason):
    from eventyay.hackathon.permissions import is_organizer
    from eventyay.hackathon.services import audit, lock_event

    if not is_organizer(user, hackathon):
        raise PermissionDenied
    hackathon = lock_event(hackathon)
    if not reason.strip():
        raise ValidationError('请填写快照用途或更正理由。')
    now = timezone.now()
    policy = material_policy(hackathon)
    entries = []
    for project in (
        Project.objects.filter(hackathon=hackathon).select_related('team__track').prefetch_related('assets', 'uploads')
    ):
        pending = [u for u in project.uploads.all() if u.state == 'uploading']
        issues = policy_submission_errors(project)
        if project.state not in (Project.State.SUBMITTED, Project.State.APPROVED):
            issues.append('当前版本尚未正式提交。')
        if pending:
            issues.append('材料按已登记意图继续续传；完成后纳入新的补充材料快照。')
        # The documented cutoff freezes registered intent, not hash computation or transfer completion.
        intent_registered = any(u.deadline_at and u.created < u.deadline_at for u in pending)
        entries.append(
            {
                'project_id': str(project.pk),
                'project_code': project.code,
                'revision': project.revision,
                'team_id': str(project.team_id),
                'team_code': project.team.code,
                'team': project.team.name,
                'captain': project.team.captain.name,
                'track_id': project.team.track_id,
                'title': project.title,
                'receipt': str(project.submission_receipt or ''),
                'state': project.state,
                'eligibility': 'registered_intent'
                if intent_registered and project.state == Project.State.QUEUED
                else 'review'
                if issues
                else 'ready',
                'issues': issues,
                'final_material_kind': project.final_material_kind,
                'assets': [asset_manifest(a) for a in project.assets.all()],
                'pending_uploads': [
                    {
                        'id': str(u.pk),
                        'kind': u.kind,
                        'sha256': u.expected_checksum,
                        'registered_at': u.created.isoformat(),
                        'checksum_bound_at': u.checksum_bound_at.isoformat() if u.checksum_bound_at else None,
                        'expires_at': u.expires_at.isoformat(),
                    }
                    for u in pending
                ],
            }
        )
    version = (SubmissionSnapshot.objects.filter(hackathon=hackathon).aggregate(value=Max('version'))['value'] or 0) + 1
    manifest = {
        'version': version,
        'captured_at': now.isoformat(),
        'policy_revision': policy.revision if policy else None,
        'submission_deadline': hackathon.submission_deadline.isoformat(),
        'freeze_at': hackathon.freeze_at.isoformat(),
        'resume_policy': '已登记任务沿用凭证续传；快照固定采集时已完成版本，后到材料只能进入新快照。',
        'entries': entries,
    }
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    snapshot = SubmissionSnapshot.objects.create(
        hackathon=hackathon,
        version=version,
        captured_at=now,
        captured_by=user,
        manifest=manifest,
        sha256=digest,
        reason=reason.strip(),
    )
    audit(hackathon, user, '生成资格材料快照', f'V{version}', digest)
    return snapshot


def retained_storage(name):
    """Keep source objects referenced by any immutable snapshot after replacements."""
    return SubmissionSnapshot.objects.filter(
        manifest__entries__contains=[{'assets': [{'storage_name': name}]}]
    ).exists()


def preflight(project):
    issues = policy_submission_errors(project)
    pending = list(project.uploads.filter(state='uploading'))
    if pending:
        issues.append(f'{len(pending)} 项上传未完成')
    return {
        'issues': issues,
        'pending': pending,
        'receipt': str(project.submission_receipt or ''),
        'next_step': '核对缺项并联系队长' if issues else '核对回执与材料，完成检查',
    }


INTENT_FIELDS = (
    'title',
    'summary',
    'description',
    'repository_url',
    'demo_url',
    'backup_url',
    'demo_instructions',
    'final_material_kind',
)


def capture_intent(project):
    return {
        'fields': {name: getattr(project, name) for name in INTENT_FIELDS},
        'assets': list(project.assets.values('kind', 'checksum')),
        'revision': project.revision,
    }


def late_submission_allowed(project, cleaned_data=None):
    if project is None or project.hackathon.results_published or timezone.now() >= project.hackathon.freeze_at:
        return False
    if project.hackathon.submissions_open:
        return False
    tasks = list(
        UploadSession.objects.filter(
            project=project, deadline_at__isnull=False, state__in=['uploading', 'complete']
        ).order_by('-created')
    )
    tasks = [task for task in tasks if task.created < task.deadline_at and task.expires_at > timezone.now()]
    if not tasks:
        return False
    fields = {name: getattr(project, name) for name in INTENT_FIELDS}
    if cleaned_data is not None and any(cleaned_data.get(name) != value for name, value in fields.items()):
        return False
    matching = [task for task in tasks if task.intent_manifest.get('fields') == fields]
    if not matching:
        return False
    captured = {(item['kind'], item['checksum']) for item in matching[0].intent_manifest.get('assets', [])}
    completed = {(task.kind, task.checksum) for task in tasks if task.state == 'complete'}
    return all((asset.kind, asset.checksum) in captured | completed for asset in project.assets.all())
