import hashlib
import json
import math
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from eventyay.hackathon.calendar import available_half_hours
from eventyay.hackathon.models import (
    AuditEntry,
    DemoSlot,
    Hackathon,
    JoinRequest,
    MatchingProfile,
    Participant,
    Project,
    RecommendationRun,
    Score,
    StaffAssignment,
    Team,
    UploadSession,
)
from eventyay.hackathon.permissions import is_organizer
from eventyay.hackathon.scoring import aggregate


def audit(hackathon, user, action, target='', detail=''):
    AuditEntry.objects.create(hackathon=hackathon, actor=user, action=action, target=str(target), detail=detail)


def lock_event(hackathon):
    return Hackathon.objects.select_for_update().get(pk=hackathon.pk)


def require_team_open(hackathon):
    if not hackathon.teams_open:
        raise ValidationError('队伍名单已冻结。如有特殊情况，请提交支持工单。')


def require_submission_open(hackathon, organizer=None, reason=''):
    if organizer is not None:
        if not is_organizer(organizer, hackathon):
            raise PermissionDenied
        if not reason.strip():
            raise ValidationError('主办方补录必须填写原因与异常发生时间。')
        if timezone.now() >= hackathon.freeze_at:
            raise ValidationError('材料已经最终冻结，不能继续补录或替换。')
        if hackathon.results_published:
            raise ValidationError('成绩已经公布，不能继续修改作品。')
        return
    if not hackathon.submissions_open:
        raise ValidationError('作品提交已截止，无法继续修改。提交异常请通过支持中心说明时间与证据。')


@transaction.atomic
def save_participant(hackathon, user, form):
    hackathon = lock_event(hackathon)
    current = Participant.objects.filter(hackathon=hackathon, user=user).first()
    if not current and timezone.now() >= hackathon.registration_deadline:
        raise ValidationError('本次活动报名已截止。')
    if current and not form.instance.created:
        raise ValidationError('你已经报名，请刷新页面修改信息。')
    participant = form.save(commit=False)
    participant.hackathon, participant.user = hackathon, user
    participant.save(update_fields=[*form.Meta.fields, 'updated'] if current else None)
    if current:
        profile = MatchingProfile.objects.filter(hackathon=hackathon, participant=participant).first()
        if profile and profile.slots is not None:
            slots = available_half_hours(hackathon, participant.unavailable_slots)
            if slots != profile.slots:
                profile.slots = slots
                profile.version += 1
                profile.save(update_fields=['slots', 'version', 'updated'])
                RecommendationRun.objects.filter(hackathon=hackathon).delete()
    audit(hackathon, user, '更新报名信息', participant.code)
    return participant


@transaction.atomic
def save_team(hackathon, participant, form):
    hackathon = lock_event(hackathon)
    require_team_open(hackathon)
    participant = Participant.objects.select_for_update().get(pk=participant.pk, hackathon=hackathon)
    if hackathon.matching_enabled and not form.instance.created:
        raise ValidationError('请在 组队广场提交完整名单并取得全员确认。')
    current = None
    if form.instance.created:
        current = Team.objects.select_for_update().get(pk=form.instance.pk, hackathon=hackathon)
        if current.captain_id != participant.pk:
            raise PermissionDenied
        if hackathon.matching_enabled and form.cleaned_data['version'] != current.roster_version:
            raise ValidationError('队伍已更新，请刷新后再修改招募资料。')
    elif participant.team_id:
        raise ValidationError('你已经加入队伍。请先离队，或由队长管理现有队伍。')
    team = form.save(commit=False)
    if current:
        for field in form.Meta.fields:
            setattr(current, field, form.cleaned_data[field])
        team = current
    if Team.objects.filter(hackathon=hackathon, name__iexact=team.name).exclude(pk=team.pk).exists():
        raise ValidationError('本次活动已有同名队伍，请换一个名称。')
    team.hackathon, team.captain = hackathon, participant
    if team.created:
        team.roster_version += 1
    team.save()
    participant.team = team
    participant.looking_for_team = False
    participant.save(update_fields=['team', 'looking_for_team', 'updated'])
    audit(hackathon, participant.user, '保存队伍', team.code)
    return team


@transaction.atomic
def join_team(hackathon, participant, team, *, invite_code=None, application=None):
    hackathon = lock_event(hackathon)
    require_team_open(hackathon)
    if hackathon.matching_enabled:
        raise ValidationError('请在 组队广场提交完整名单并取得全员确认。')
    team = Team.objects.select_for_update().get(pk=team.pk, hackathon=hackathon)
    participant = Participant.objects.select_for_update().get(pk=participant.pk, hackathon=hackathon)
    if invite_code != team.invite_code and application is None:
        raise PermissionDenied
    if participant.team_id:
        raise ValidationError('此选手已经加入队伍，不能重复加入。')
    if team.members.count() >= hackathon.team_size:
        raise ValidationError('队伍已满，请选择其他队伍。')
    participant.team = team
    participant.looking_for_team = False
    participant.save(update_fields=['team', 'looking_for_team', 'updated'])
    JoinRequest.objects.filter(participant=participant, state='pending').update(state='declined')
    if application:
        JoinRequest.objects.filter(pk=application.pk, team=team, participant=participant).update(state='accepted')
    audit(hackathon, participant.user, '加入队伍', team.code)


@transaction.atomic
def leave_team(hackathon, participant):
    hackathon = lock_event(hackathon)
    if hackathon.matching_enabled:
        raise ValidationError('请在 组队广场按名单政策退出队伍。')
    require_team_open(hackathon)
    participant = Participant.objects.select_for_update(of=('self',)).select_related('team').get(pk=participant.pk)
    if not participant.team_id:
        raise ValidationError('你还没有加入队伍。')
    if participant.team.captain_id == participant.pk:
        raise ValidationError('队长需要先将队长身份转交给一名队友，再离队。')
    audit(hackathon, participant.user, '离开队伍', participant.team.code)
    participant.team = None
    participant.save(update_fields=['team', 'updated'])


def submission_errors(project, pending_kinds=()):
    errors = []
    if (
        project.created
        and not pending_kinds
        and UploadSession.objects.filter(project=project, state='uploading', expires_at__gt=timezone.now()).exists()
    ):
        errors.append('还有材料正在上传，请完成上传或取消任务后再正式提交。')
    kinds = set(project.assets.values_list('kind', flat=True)) if project.created else set()
    kinds.update(pending_kinds)
    if not project.description.strip():
        errors.append('请说明已实现的功能、使用方式和项目价值。')
    if not project.repository_url:
        errors.append('请填写代码仓库，并确认评委有访问权限。')
    if not project.demo_url and 'video' not in kinds:
        errors.append('请提供可访问的 Demo 或上传演示录屏。')
    if not project.backup_url and 'video' not in kinds:
        errors.append('请提供备用演示链接或上传录屏。')
    if 'poster' not in kinds:
        errors.append('请上传电子项目海报，不能用现场立牌照片替代。')
    if not project.demo_instructions.strip():
        errors.append('请填写实机演示步骤与设备需求。')
    if not project.checklist:
        errors.append('请完成并勾选提交自查。')
    return errors


def require_project_editable(project, allow_queued=False):
    if project and project.state == Project.State.QUEUED and not allow_queued:
        raise ValidationError('作品已登记排队提交，信息与材料清单已锁定。请等待上传完成；需要修改时先取消排队提交。')
    if project and (
        DemoSlot.objects.filter(project=project, state__in=['live', 'done']).exists()
        or Score.objects.filter(project=project, final=True).exists()
    ):
        raise ValidationError('作品已开始路演或已有确认评分，不能再修改参评版本。')


@transaction.atomic
def save_project(hackathon, participant, form, submit=False, organizer=None, reason=''):
    hackathon = lock_event(hackathon)
    require_submission_open(hackathon, organizer, reason)
    participant = Participant.objects.select_related('team').get(pk=participant.pk, hackathon=hackathon)
    if not participant.team_id or participant.team.captain_id != participant.pk:
        raise PermissionDenied
    current = Project.objects.select_for_update().filter(team=participant.team).first()
    if (
        current
        and current.state == Project.State.QUEUED
        and submit
        and (
            form.cleaned_data['version'] + 1 == current.revision
            and all(getattr(current, name) == form.cleaned_data[name] for name in form.Meta.fields)
        )
    ):
        return current
    require_project_editable(current)
    if current and (not form.instance.created or form.cleaned_data['version'] != current.revision):
        expected_state = Project.State.SUBMITTED if submit else Project.State.DRAFT
        if (
            form.cleaned_data['version'] + 1 == current.revision
            and (current.state == expected_state or (not submit and current.state == Project.State.CHANGES))
            and all(getattr(current, name) == form.cleaned_data[name] for name in form.Meta.fields)
        ):
            return current
        raise ValidationError('作品已在其他页面被更新。请刷新后再修改，避免覆盖最新内容。')
    project = form.save(commit=False)
    project.hackathon, project.team = hackathon, participant.team
    if submit:
        pending = (
            list(
                UploadSession.objects.filter(
                    project=project,
                    state='uploading',
                    expires_at__gt=timezone.now(),
                )
            )
            if current
            else []
        )
        errors = submission_errors(project, pending_kinds=[task.kind for task in pending])
        if errors:
            raise ValidationError(errors)
        project.submission_intent_at = timezone.now()
        project.submission_client_clicked_at = form.cleaned_data.get('client_clicked_at')
        project.submission_receipt = uuid.uuid4()
        project.submission_manifest = {
            'fields': {name: form.cleaned_data[name] for name in form.Meta.fields},
            'uploads': [{'id': str(task.pk), 'sha256': task.expected_checksum} for task in pending],
            'assets': list(project.assets.values('kind', 'checksum')) if current else [],
        }
        project.submission_manifest_hash = hashlib.sha256(
            json.dumps(project.submission_manifest, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        project.state = Project.State.QUEUED if pending else Project.State.SUBMITTED
        if not pending:
            project.submitted_at = project.submitted_at or timezone.now()
    elif current and current.state != Project.State.DRAFT:
        project.state = Project.State.CHANGES
    project.revision = (current.revision if current else 0) + 1
    project.equipment_checked = False
    project.save()
    DemoSlot.objects.filter(project=project, state='ready').update(state='waiting')
    audit(
        hackathon,
        organizer or participant.user,
        ('登记排队提交' if project.state == Project.State.QUEUED else '正式提交作品') if submit else '保存作品草稿',
        project.code,
        f'版本 {project.revision}'
        + (f' · 主办方补录：{reason}' if organizer else '')
        + (f' · 凭证 {project.submission_receipt} · SHA-256 {project.submission_manifest_hash}' if submit else ''),
    )
    return project


def complete_queued_project(project, user):
    """Caller holds the event/project locks; upload IDs form the frozen manifest."""
    if project.state != Project.State.QUEUED:
        return
    expected = project.submission_manifest['uploads']
    tasks = list(UploadSession.objects.filter(project=project, pk__in=[item['id'] for item in expected]))
    if len(tasks) != len(expected) or any(task.state != 'complete' for task in tasks):
        return
    if submission_errors(project):
        return
    project.state = Project.State.SUBMITTED
    project.submitted_at = project.submitted_at or timezone.now()
    project.save(update_fields=['state', 'submitted_at', 'updated'])
    audit(project.hackathon, user, '排队材料到齐，正式提交', project.code, str(project.submission_receipt))


def invalidate_queued_project(session):
    """Call with the session lock held, before locking event/project."""
    lock_event(session.hackathon)
    project = Project.objects.select_for_update().get(pk=session.project_id)
    if project.state == Project.State.QUEUED:
        project.state = Project.State.CHANGES
        project.save(update_fields=['state', 'updated'])
        audit(session.hackathon, session.user, '排队提交中止', project.code, str(project.submission_receipt))


def save_asset(hackathon, participant, project, form, organizer=None, reason=''):
    # Uploads uses the event permission/locking helpers in this module.
    from eventyay.hackathon.uploads import save_direct_asset

    return save_direct_asset(hackathon, participant, project, form, organizer=organizer, reason=reason)


@transaction.atomic
def save_score(hackathon, user, project, form):
    hackathon = lock_event(hackathon)
    project = Project.objects.select_related('team').get(pk=project.pk, hackathon=hackathon)
    if not hackathon.judging_open or hackathon.results_published:
        raise ValidationError('当前未开放评分，或结果已公布并锁定。')
    if not StaffAssignment.objects.filter(hackathon=hackathon, user=user, role='judge').exists():
        raise PermissionDenied
    if Participant.objects.filter(hackathon=hackathon, user=user, team=project.team).exists():
        raise ValidationError('不能为自己的队伍评分，请回避。')
    if project.state != Project.State.APPROVED:
        raise ValidationError('只有通过材料检查的作品可以评分。')
    score = form.save(commit=False)
    current = Score.objects.filter(project=project, judge=user).first()
    if (form.cleaned_data.get('revision') or 0) != (current.revision if current else 0):
        raise ValidationError('此评分已在其他页面保存。请重新打开评分页核对，当前输入未覆盖已保存的分数。')
    if (form.cleaned_data.get('scoring_revision') or 0) != hackathon.scoring_revision:
        raise ValidationError('算分规则已更新，请重新打开评分页后按新规则评分。当前输入尚未保存。')
    if current:
        score.pk = current.pk
    score.hackathon, score.project, score.judge = hackathon, project, user
    score.revision = (current.revision if current else 0) + 1
    if score.final:
        for nomination in ('breakthrough', 'creativity'):
            if (
                getattr(score, nomination)
                and Score.objects.filter(
                    hackathon=hackathon,
                    judge=user,
                    final=True,
                    **{nomination: True},
                )
                .exclude(project=project)
                .count()
                >= 3
            ):
                raise ValidationError('每个奖项最多确认提名 3 个项目，请先取消一个已有提名。')
    score.save()
    audit(hackathon, user, '确认评分' if score.final else '保存评分草稿', project.code)
    return score


@transaction.atomic
def generate_queue(hackathon, user):
    hackathon = lock_event(hackathon)
    position = DemoSlot.objects.filter(hackathon=hackathon).aggregate(value=Max('position'))['value'] or 0
    for project in Project.objects.filter(hackathon=hackathon, state='approved', slot__isnull=True).order_by(
        'submitted_at'
    ):
        position += 1
        DemoSlot.objects.create(hackathon=hackathon, project=project, position=position)
    audit(hackathon, user, '补全路演队列')


def clock_state(hackathon, slot):
    if not slot or not slot.started_at:
        return {'phase': 'idle', 'remaining': hackathon.demo_seconds, 'running': False}
    elapsed = max(0, (timezone.now() - slot.started_at).total_seconds())
    if slot.paused_remaining is not None:
        return {'phase': 'paused', 'remaining': slot.paused_remaining, 'running': False}
    if elapsed < hackathon.demo_seconds:
        return {'phase': 'demo', 'remaining': math.ceil(hackathon.demo_seconds - elapsed), 'running': True}
    remaining = max(0, math.ceil(hackathon.demo_seconds + hackathon.transition_seconds - elapsed))
    return {'phase': 'transition' if remaining else 'finished', 'remaining': remaining, 'running': remaining > 0}


@transaction.atomic
def stage_action(hackathon, user, slot, action, note=''):
    hackathon = lock_event(hackathon)
    slot = DemoSlot.objects.select_related('project').get(pk=slot.pk, hackathon=hackathon)
    if action == 'ready':
        if slot.state not in {DemoSlot.State.WAITING, DemoSlot.State.DEFERRED}:
            raise ValidationError('仅待候场或顺延的队伍可以标为就绪。')
        if slot.project.state != Project.State.APPROVED or not slot.project.equipment_checked:
            raise ValidationError('请先通过材料检查并完成设备演练。')
        slot.state = DemoSlot.State.READY
    elif action == 'start':
        if slot.state != DemoSlot.State.READY:
            raise ValidationError('请先完成设备检查，并将队伍标为准备就绪。')
        if DemoSlot.objects.filter(hackathon=hackathon, state='live').exists():
            raise ValidationError('请先结束当前路演，再开始下一队。')
        if slot.project.state != Project.State.APPROVED or not slot.project.equipment_checked:
            raise ValidationError('作品材料发生变化，请重新检查并确认设备演练。')
        slot.state, slot.started_at, slot.paused_remaining = DemoSlot.State.LIVE, timezone.now(), None
    elif action in {'pause', 'resume', 'finish'}:
        if slot.state != DemoSlot.State.LIVE:
            raise ValidationError('该队伍当前不在展示中。')
        state = clock_state(hackathon, slot)
        if action == 'pause':
            if state['phase'] != 'demo':
                raise ValidationError('只有展示进行中才能暂停。')
            slot.paused_remaining = state['remaining']
        elif action == 'resume':
            if slot.paused_remaining is None:
                raise ValidationError('计时器没有暂停。')
            slot.started_at = timezone.now() - timedelta(seconds=hackathon.demo_seconds - slot.paused_remaining)
            slot.paused_remaining = None
        else:
            slot.state = DemoSlot.State.DONE
            slot.paused_remaining = None
    elif action in {'defer', 'up', 'down'}:
        if slot.state in {DemoSlot.State.LIVE, DemoSlot.State.DONE}:
            raise ValidationError('展示中或已完成的队伍不能调整顺序。')
        if action == 'defer':
            if not note.strip():
                raise ValidationError('请填写顺延原因，例如参加开学典礼或设备故障。')
            slot.state, slot.note = DemoSlot.State.DEFERRED, note[:250]
            slot.position = (DemoSlot.objects.filter(hackathon=hackathon).aggregate(v=Max('position'))['v'] or 0) + 1
        else:
            others = DemoSlot.objects.filter(hackathon=hackathon).exclude(state__in=['live', 'done'])
            other = (
                others.filter(position__lt=slot.position).order_by('-position').first()
                if action == 'up'
                else others.filter(position__gt=slot.position).order_by('position').first()
            )
            if other:
                other.position, slot.position = slot.position, other.position
                other.save(update_fields=['position', 'updated'])
    else:
        raise ValidationError('未知路演操作。')
    slot.save()
    audit(hackathon, user, f'路演：{action}', slot.project.code, note)


def results(hackathon):
    judges = set(
        StaffAssignment.objects.filter(
            hackathon=hackathon, role='judge', user__is_active=True, user__is_spam=False
        ).values_list('user_id', flat=True)
    )
    conflicts = {}
    for participant in Participant.objects.filter(hackathon=hackathon, user_id__in=judges, team__isnull=False):
        conflicts.setdefault(participant.team_id, set()).add(participant.user_id)
    rows = []
    for project in (
        Project.objects.filter(hackathon=hackathon, state='approved')
        .select_related('team__track')
        .prefetch_related('scores')
    ):
        eligible = judges - conflicts.get(project.team_id, set())
        scores = [score for score in project.scores.all() if score.final and score.judge_id in eligible]
        average, counted = aggregate(scores, hackathon)
        rows.append(
            {
                'project': project,
                'average': average,
                'count': len(scores),
                'expected': len(eligible),
                'complete': bool(eligible) and len(scores) == len(eligible) and average is not None,
                'counted': counted,
                'trimmed': len(scores) - counted if average is not None else 0,
                'breakthrough': sum(score.breakthrough for score in scores),
                'creativity': sum(score.creativity for score in scores),
            }
        )
    rows.sort(key=lambda row: (not row['complete'], -(row['average'] or 0), row['project'].title))
    last_score, rank = None, 0
    for index, row in enumerate(rows, 1):
        if row['complete']:
            if row['average'] != last_score:
                rank = index
            row['rank'], last_score = rank, row['average']
    return rows
