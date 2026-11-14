import hashlib
import json
import math
import secrets
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone

from eventyay.hackathon.calendar import available_half_hours
from eventyay.hackathon.models import (
    Announcement,
    AuditEntry,
    DemoSlot,
    Hackathon,
    JoinRequest,
    MatchingProfile,
    Participant,
    Project,
    RecommendationRun,
    Resource,
    ScheduleItem,
    Score,
    StaffAssignment,
    StationToken,
    Team,
    Track,
    UploadSession,
)
from eventyay.hackathon.permissions import is_organizer
from eventyay.hackathon.scoring import aggregate, is_trimmed


def audit(hackathon, user, action, target='', detail=''):
    AuditEntry.objects.create(hackathon=hackathon, actor=user, action=action, target=str(target), detail=detail)


def lock_event(hackathon):
    return Hackathon.objects.select_for_update().get(pk=hackathon.pk)


def set_check_in(hackathon, actor, person, checked_in):
    """按条件更新签到状态，返回是否真的改动了。

    两个主办同时点签到时，只有真正把状态改过来的那一个写审计；另一个人
    拿到的影响行数是 0，不会再写一条「看起来像操作了两次」的日志。
    """
    updated = Participant.objects.filter(pk=person.pk, checked_in=not checked_in).update(
        checked_in=checked_in, updated=timezone.now()
    )
    if updated:
        audit(hackathon, actor, '签到' if checked_in else '撤销签到', person.code)
    return bool(updated)


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


def bind_participant_email(form, participant):
    """The bound email lives on the user row; an empty submission keeps the current
    binding, because unbinding would silently drop the password-recovery channel."""
    email = form.cleaned_data.get('email')
    if email and participant.user_id and participant.user.email != email:
        participant.user.email = email
        participant.user.save(update_fields=['email'])


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
    bind_participant_email(form, participant)
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


@transaction.atomic
def dissolve_team(hackathon, participant):
    """Let a captain who is alone in the team step out before the freeze.

    A team with other members still needs a handover, and a team that already
    built a project needs the organizer: dissolving would take the project
    with it, so the exit stays available only while nothing depends on it.
    """
    hackathon = lock_event(hackathon)
    if hackathon.matching_enabled:
        raise ValidationError('请在 组队广场按名单政策退出队伍。')
    require_team_open(hackathon)
    participant = Participant.objects.select_for_update(of=('self',)).select_related('team').get(pk=participant.pk)
    team = participant.team
    if team is None:
        raise ValidationError('你还没有队伍。')
    if team.captain_id != participant.pk:
        raise PermissionDenied
    if team.members.exclude(pk=participant.pk).exists():
        raise ValidationError('队伍里还有队友，请先交接队长或让队友离队。')
    if Project.objects.filter(team=team).exists():
        raise ValidationError('这支队伍已经建过作品，不能解散。请联系主办方处理。')
    team.state = 'dissolved'
    team.recruiting = False
    team.save(update_fields=['state', 'recruiting', 'updated'])
    JoinRequest.objects.filter(team=team, state='pending').update(state='declined')
    participant.team = None
    participant.looking_for_team = True
    participant.save(update_fields=['team', 'looking_for_team', 'updated'])
    audit(hackathon, participant.user, '解散队伍', team.code)


@transaction.atomic
def withdraw_application(hackathon, participant, application):
    """The applicant can take a pending request back; the captain keeps their own list."""
    hackathon = lock_event(hackathon)
    require_team_open(hackathon)
    application = JoinRequest.objects.select_for_update().get(pk=application.pk, hackathon=hackathon)
    if application.participant_id != participant.pk:
        raise PermissionDenied
    if application.state != 'pending':
        raise ValidationError('这条申请已经有结果，不能再撤回。')
    application.state = 'withdrawn'
    application.save(update_fields=['state', 'updated'])
    audit(hackathon, participant.user, '撤回组队申请', application.team.code)


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


def project_status(project, uploads=None):
    """What “have we submitted” means right now, for every role and view.

    ``submitted_at`` only ever records the first formal submission, so it cannot
    answer that question for the current version; ``state`` plus ``state_reason`` can.
    """
    if project is None:
        return {
            'key': 'missing',
            'label': '尚未创建作品',
            'detail': '先填写作品信息并保存草稿，即可上传海报与演示材料。',
            'action': '开始填写作品',
            'done': False,
            'tone': 'neutral',
        }
    state = project.state
    if state == Project.State.QUEUED:
        tasks = project.uploads.filter(state='uploading') if uploads is None else uploads
        stalled = any(
            task.expires_at <= timezone.now() for task in tasks
        ) or project.hackathon.freeze_at <= timezone.now()
        if stalled:
            return {
                'key': 'upload_stalled',
                'label': '排队提交已过期',
                'detail': '登记后仍有材料未在续传期限内完成，本次排队登记已中止。请重新上传并再次正式提交。',
                'action': '继续上传材料',
                'done': False,
                'tone': 'error',
            }
        return {
            'key': 'queued',
            'label': project.get_state_display(),
            'detail': '服务器已登记本次提交意图；材料全部上传并通过校验后才会正式提交。',
            'action': '查看上传进度',
            'done': False,
            'tone': 'info',
        }
    if state == Project.State.CHANGES:
        if project.state_reason == Project.StateReason.EDITED:
            return {
                'key': 'resubmit',
                'label': '有修改尚未正式提交',
                'detail': '当前版本还没有再次正式提交；参评的仍是上一次正式提交的版本。',
                'action': '提交修改',
                'done': False,
                'tone': 'warning',
            }
        if project.state_reason == Project.StateReason.UPLOAD:
            return {
                'key': 'upload_stalled',
                'label': '上传未完成',
                'detail': '排队登记已因上传取消或超时而中止，当前版本尚未正式提交。请补齐材料后再次正式提交。',
                'action': '继续上传材料',
                'done': False,
                'tone': 'error',
            }
        if project.state_reason == Project.StateReason.REVIEW:
            return {
                'key': 'review',
                'label': '主办方要求补充',
                'detail': project.review_note.strip() or '请查看作品页的检查意见，逐项补齐后再次正式提交。',
                'action': '定位缺项并补充',
                'done': False,
                'tone': 'warning',
            }
        return {
            'key': 'changes',
            'label': project.get_state_display(),
            'detail': '作品被标为需要补充。请查看检查意见；若你刚保存过修改或替换过材料，也需要再次正式提交。',
            'action': '查看检查意见',
            'done': False,
            'tone': 'warning',
        }
    if state == Project.State.SUBMITTED:
        return {
            'key': 'submitted',
            'label': '当前版本已正式提交',
            'detail': '当前版本已正式提交，等待主办方检查；检查通过不代表作品已获展示授权。',
            'action': '查看提交凭证',
            'done': True,
            'tone': 'success',
        }
    if state == Project.State.APPROVED:
        return {
            'key': 'approved',
            'label': '当前提交版本已通过检查',
            'detail': '当前提交版本已通过检查，可留意路演安排。',
            'action': '查看路演安排',
            'done': True,
            'tone': 'success',
        }
    return {
        'key': 'draft',
        'label': project.get_state_display(),
        'detail': '草稿已保存，尚未正式提交。由队长点击“正式提交 / 排队登记”后才会进入评审。',
        'action': '补齐材料并正式提交',
        'done': False,
        'tone': 'neutral',
    }


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
        project.state_reason = ''
        if not pending:
            project.submitted_at = project.submitted_at or timezone.now()
    elif current and current.state != Project.State.DRAFT:
        project.state = Project.State.CHANGES
        project.state_reason = Project.StateReason.EDITED
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
    project.state_reason = ''
    project.submitted_at = project.submitted_at or timezone.now()
    project.save(update_fields=['state', 'state_reason', 'submitted_at', 'updated'])
    audit(project.hackathon, user, '排队材料到齐，正式提交', project.code, str(project.submission_receipt))


def invalidate_queued_project(session):
    """Call with the session lock held, before locking event/project."""
    lock_event(session.hackathon)
    project = Project.objects.select_for_update().get(pk=session.project_id)
    if project.state == Project.State.QUEUED:
        project.state = Project.State.CHANGES
        project.state_reason = Project.StateReason.UPLOAD
        project.save(update_fields=['state', 'state_reason', 'updated'])
        audit(session.hackathon, session.user, '排队提交中止', project.code, str(project.submission_receipt))


def save_asset(hackathon, participant, project, form, organizer=None, reason=''):
    # Uploads uses the event permission/locking helpers in this module.
    from eventyay.hackathon.uploads import save_direct_asset

    return save_direct_asset(hackathon, participant, project, form, organizer=organizer, reason=reason)


@transaction.atomic
def save_score(hackathon, user, project, form, confirm=False):
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
    score.final = confirm
    if confirm and not score.is_complete:
        raise ValidationError('四项分数都填好后才能确认评分；空着的项目不会被算成 0 分。')
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
    if hackathon.results_published:
        raise ValidationError('成绩已公布，路演队列已锁定。')
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
    # Once results are published the queue is locked, but an organizer must still be
    # able to bring a slot that is already on air to a close (pause/resume/finish).
    winding_down = action in {'pause', 'resume', 'finish'} and slot.state == DemoSlot.State.LIVE
    if hackathon.results_published and not winding_down:
        raise ValidationError('成绩已公布，路演队列已锁定。')
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
            slot.ended_at = timezone.now()
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
    elif action == 'reset':
        if slot.state not in {DemoSlot.State.LIVE, DemoSlot.State.DONE}:
            raise ValidationError('仅展示中或已完成的队伍可以重新排队。')
        if not note.strip():
            raise ValidationError('请填写重新排队的原因，例如设备故障或时间调整。')
        slot.state = DemoSlot.State.WAITING
        slot.started_at = None
        slot.ended_at = None
        slot.paused_remaining = None
        slot.note = note[:250]
    else:
        raise ValidationError('未知路演操作。')
    slot.save()
    audit(hackathon, user, f'路演：{action}', slot.project.code, note)


# 现场助手连续多久没有拉取状态就算「连接中断」。
STATION_STALE_SECONDS = 90
# 助手轮询很密，最近同步时间按这个间隔落库，避免每次轮询都写一行。
STATION_TOUCH_SECONDS = 30


def issue_station_token(hackathon, user):
    """签发新的播放助手令牌：旧令牌立即失效，明文只在这一次返回。

    先锁活动行再换，保证同一活动同时只有一枚有效令牌：两个主办同时点
    「确认更换令牌」时，后一个会看到前一个刚签发的那枚被作废，而不是
    两枚同时有效。服务端只保存摘要；调用方负责把明文只展示一次。
    """
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        hackathon = lock_event(hackathon)
        StationToken.objects.filter(hackathon=hackathon).delete()
        station = StationToken.objects.create(
            hackathon=hackathon,
            digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=12),
            created_by=user,
        )
        audit(hackathon, user, '签发播放助手令牌', station.pk, '旧令牌已立即失效，现场助手需要重新连接')
    return token


def touch_station(hackathon):
    """现场助手拉取状态时刷新「最近同步时间」。"""
    now = timezone.now()
    StationToken.objects.filter(hackathon=hackathon).filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=now - timedelta(seconds=STATION_TOUCH_SECONDS))
    ).update(last_seen_at=now)


def station_status(hackathon):
    """播放助手的连接状态：未连接 / 最近同步正常 / 连接中断 / 已过期。"""
    station = StationToken.objects.filter(hackathon=hackathon).first()
    now = timezone.now()
    status = {
        'state': 'none',
        'label': '未连接',
        'detail': '还没有签发播放助手令牌。',
        'tone': '',
        'has_token': False,
        'last_seen_at': None,
        'expires_at': None,
    }
    if not station:
        return status
    status['has_token'] = True
    status['expires_at'] = station.expires_at
    status['last_seen_at'] = station.last_seen_at
    if station.expires_at <= now:
        status.update(
            state='expired',
            label='已过期',
            tone='warning',
            detail='令牌已过期，现场助手需要用新令牌重新连接。',
        )
    elif station.last_seen_at is None:
        status.update(detail='令牌已签发，但还没有现场助手连上来。')
    elif (now - station.last_seen_at).total_seconds() > STATION_STALE_SECONDS:
        status.update(
            state='stale',
            label='连接中断',
            tone='warning',
            detail='现场助手已经有一段时间没有同步。',
        )
    else:
        status.update(
            state='ok',
            label='最近同步正常',
            tone='success',
            detail='现场助手正在正常拉取队列与材料。',
        )
    return status


def station_rotation_impact(hackathon):
    """更换令牌前先说清楚会发生什么，别让现场的人事后才发现连接断了。"""
    station = StationToken.objects.filter(hackathon=hackathon).first()
    if not station:
        return {
            'state': 'none',
            'text': '当前没有生效的播放助手令牌，本次签发不会让任何连接失效。',
        }
    if station.expires_at <= timezone.now():
        return {
            'state': 'expired',
            'text': '当前令牌已经过期，现场助手本就无法连接；更换不会额外影响任何正在工作的连接。',
        }
    return {
        'state': 'active',
        'text': (
            '现场助手正在使用当前令牌。更换后旧令牌立即失效，'
            '现场电脑需要填入新令牌重新连接，才能继续拉取材料与状态。'
        ),
    }


def judge_panel(hackathon):
    """Active judges and the teams each of them has to avoid — the single source of 'who owes which score'."""
    assignments = list(
        StaffAssignment.objects.filter(
            hackathon=hackathon, role='judge', user__is_active=True, user__is_spam=False
        ).select_related('user')
    )
    conflicts = {}
    for participant in Participant.objects.filter(
        hackathon=hackathon, user_id__in=[assignment.user_id for assignment in assignments], team__isnull=False
    ):
        conflicts.setdefault(participant.team_id, set()).add(participant.user_id)
    return assignments, conflicts


def active_judge_ids(hackathon):
    """Judges the scoring actually counts: assigned to this event, still active, not marked as spam."""
    return {assignment.user_id for assignment in judge_panel(hackathon)[0]}


def results(hackathon):
    assignments, conflicts = judge_panel(hackathon)
    judges = {assignment.user_id for assignment in assignments}
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
                'thin_panel': is_trimmed(hackathon) and 0 < len(scores) < 2 * hackathon.trim_extremes + 1,
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


def review_progress(hackathon):
    """Who still owes which score: same judge panel and same task set that drives the ranking."""
    assignments, conflicts = judge_panel(hackathon)
    projects = list(Project.objects.filter(hackathon=hackathon, state='approved').select_related('team__track'))
    scores = {
        (score.judge_id, score.project_id): score
        for score in Score.objects.filter(
            hackathon=hackathon, judge_id__in=[a.user_id for a in assignments], project_id__in=[p.pk for p in projects]
        )
    }
    rows = []
    for assignment in assignments:
        expected = confirmed = drafts = 0
        outstanding = []
        for project in projects:
            if assignment.user_id in conflicts.get(project.team_id, set()):
                continue
            expected += 1
            score = scores.get((assignment.user_id, project.pk))
            if score is None:
                outstanding.append({'project': project, 'draft': False})
            elif score.final:
                confirmed += 1
            else:
                drafts += 1
                outstanding.append({'project': project, 'draft': True})
        rows.append(
            {
                'user': assignment.user,
                'expected': expected,
                'confirmed': confirmed,
                'drafts': drafts,
                'outstanding': outstanding,
                'complete': not outstanding,
            }
        )
    rows.sort(key=lambda row: (row['complete'], -len(row['outstanding']), row['user'].fullname or row['user'].email))
    return rows


def judge_removal_impact(hackathon, assignment):
    """移除一位工作人员前先算清楚影响：计分资格、已有评分、缺评数量。

    统计口径与 results() / review_progress() 一致：有效评委集合、回避规则、
    只认已确认的评分。特别注意「回避」：评委与某支队伍同队时，他对该作品
    本来就不算应评、他的评分本来就不进统计——这种评分既不能算成「移除后
    少掉的分数」，也不该让作品看起来变缺评。这里不改任何数据，只回答
    「移除之后会少什么、会不会缺评」。
    """
    impact = {
        'assignment': assignment,
        'user': assignment.user,
        'role': assignment.role,
        'scoring': assignment.role == 'judge',
        'items': [],
        'confirmed': 0,
        'drafts': 0,
        'expected_before': 0,
        'expected_after': 0,
        'panel_size': 0,
        'new_panel_size': 0,
        'becoming_incomplete': 0,
        'becoming_complete': 0,
        'still_incomplete': 0,
        'in_panel': False,
        'note': '',
    }
    if assignment.role != 'judge':
        impact['note'] = '志愿者不参与计分，移除不会改变评分结果，也不会改变缺评检查。'
        return impact
    assignments, conflicts = judge_panel(hackathon)
    panel = {item.user_id for item in assignments}
    impact['panel_size'] = len(panel)
    impact['in_panel'] = assignment.user_id in panel
    impact['new_panel_size'] = len(panel) - 1 if impact['in_panel'] else len(panel)
    if not impact['in_panel']:
        impact['note'] = '这位评委当前不在有效计分集合里（账号停用或被标记），移除只影响访问权限，不改变评分结果。'
    projects = list(Project.objects.filter(hackathon=hackathon, state='approved').select_related('team'))
    confirmed_by_project = {}
    for project_id, judge_id in Score.objects.filter(hackathon=hackathon, final=True).values_list(
        'project_id', 'judge_id'
    ):
        confirmed_by_project.setdefault(project_id, set()).add(judge_id)
    mine = {score.project_id: score for score in Score.objects.filter(hackathon=hackathon, judge=assignment.user)}
    for project in projects:
        eligible = panel - conflicts.get(project.team_id, set())
        score = mine.get(project.pk)
        # 这位评委对这个作品是否算应评：被回避时不算，他的评分本来也不进统计。
        judge_counts = assignment.user_id in eligible
        counted_final = judge_counts and score is not None and score.final
        confirmed_before = len(confirmed_by_project.get(project.pk, set()) & eligible)
        eligible_after = eligible - {assignment.user_id}
        confirmed_after = confirmed_before - (1 if counted_final else 0)
        missing_before = max(0, len(eligible) - confirmed_before)
        missing_after = max(0, len(eligible_after) - confirmed_after)
        impact['expected_before'] += len(eligible)
        impact['expected_after'] += len(eligible_after)
        if counted_final:
            impact['confirmed'] += 1
        elif judge_counts and score is not None:
            impact['drafts'] += 1
        if missing_after > 0:
            if missing_before == 0:
                impact['becoming_incomplete'] += 1
            else:
                impact['still_incomplete'] += 1
        elif missing_before > 0:
            # 应评人数变少、已确认份数没变，这个作品反而变完整了。
            impact['becoming_complete'] += 1
        impact['items'].append(
            {
                'project': project,
                'confirmed': counted_final,
                'draft': judge_counts and score is not None and not score.final,
                'counted': judge_counts,
                'expected_after': len(eligible_after),
                'missing_after': missing_after,
            }
        )
    return impact


def event_url(hackathon, name, **kwargs):
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': hackathon.slug, **kwargs})


PUBLISH_ACTIONS = {
    'open-event': ('is_public', True),
    'hide-event': ('is_public', False),
    'open-gallery': ('gallery_open', True),
    'close-gallery': ('gallery_open', False),
    'open-judging': ('judging_open', True),
    'close-judging': ('judging_open', False),
    'publish-results': ('results_published', True),
    'unpublish-results': ('results_published', False),
}

PUBLISH_SWITCHES = (
    ('is_public', '活动公开', 'open-event', 'hide-event', '所有访客与已登录选手',
     '活动页、日程、通知和教程对所有人可读；关闭后只有主办与工作人员能进。'),
    ('gallery_open', '作品展厅', 'open-gallery', 'close-gallery', '所有访客',
     '公开已通过审核且本人同意展示的作品；未勾选同意展示的作品仍然不可见。'),
    ('judging_open', '评委打分', 'open-judging', 'close-judging', '评委',
     '评委可进入打分页；关闭后打分页只读，已保存的分数全部保留。'),
    ('results_published', '成绩公布', 'publish-results', 'unpublish-results',
     '所有访客与全部评委', '公开名次、平均分与评语；公布后评分、作品审核与路演调度即被锁定。'),
)

PUBLISH_LABELS = {item[0]: item[1] for item in PUBLISH_SWITCHES}

DEADLINE_ORDER = (
    ('registration_deadline', '报名截止', True),
    ('team_freeze_at', '队伍冻结', True),
    ('soft_deadline', '建议提交时间', False),
    ('submission_deadline', '作品提交截止', False),
    ('freeze_at', '材料最终冻结', False),
)


def publish_blockers(current, field, opening, rows=None):
    """Conditions that must hold before a switch may take effect; closing a switch is never blocked."""
    if not opening:
        return []
    if field == 'is_public':
        blockers = []
        if not Track.published(current).exists():
            blockers.append('还没有已发布的赛道，公开活动后选手看不到任何赛题。')
        if current.event.date_from and current.event.date_to and current.event.date_to <= current.event.date_from:
            blockers.append('活动结束时间必须晚于开始时间，请先修正活动设置里的日期。')
        return blockers
    if field == 'gallery_open':
        if not Project.objects.filter(hackathon=current, state='approved', public_consent=True).exists():
            return ['还没有同意公开且已通过审核的作品，展厅开放后只是空页面。']
        return []
    if field == 'judging_open':
        blockers = []
        if not active_judge_ids(current):
            blockers.append('还没有可用评委（账户已停用或标为垃圾的不计入），请先到工作人员页添加评审角色。')
        if not Project.objects.filter(hackathon=current, state='approved').exists():
            blockers.append('还没有通过审核的作品，评委进场后无队可评。')
        return blockers
    if field == 'results_published':
        rows = results(current) if rows is None else rows
        if not rows:
            return ['还没有进入评审的作品，暂无成绩可公布。']
        if any(not row['complete'] for row in rows):
            return ['还有作品未完成全部有效评委评分，暂不能公布成绩。']
    return []


def set_publish_flag(hackathon, user, action):
    """One switch per explicit action; ordinary settings saves never reach this code."""
    if action not in PUBLISH_ACTIONS:
        raise ValidationError('未知的发布操作，本次未改变任何开关。')
    field, value = PUBLISH_ACTIONS[action]
    with transaction.atomic():
        current = lock_event(hackathon)
        blockers = publish_blockers(current, field, value)
        if blockers:
            raise ValidationError(blockers)
        setattr(current, field, value)
        current.save(update_fields=[field])
        label = PUBLISH_LABELS[field]
        audit(current, user, f'{"开启" if value else "关闭"}{label}', detail=label)
    return label, value


def publish_plan(hackathon):
    rows = results(hackathon)
    return [
        {
            'field': field,
            'label': label,
            'audience': audience,
            'effect': effect,
            'value': getattr(hackathon, field),
            'open_action': open_action,
            'close_action': close_action,
            'blockers': publish_blockers(hackathon, field, True, rows=rows),
        }
        for field, label, open_action, close_action, audience, effect in PUBLISH_SWITCHES
    ]


def draft_content(hackathon):
    """Unpublished organizer content, so a publish click is not made blind."""
    return [
        {'kind': kind, 'label': label, 'count': model.objects.filter(hackathon=hackathon, state='draft').count()}
        for kind, label, model in (
            ('announcement', '通知', Announcement),
            ('resource', '教程与指南', Resource),
            ('schedule', '日程', ScheduleItem),
            ('track', '赛道', Track),
        )
    ]


def date_drift(hackathon):
    """Dates that no longer line up after the event window moves; never shifted automatically."""
    start, end = hackathon.event.date_from, hackathon.event.date_to
    drift = []
    previous_label, previous = '', None
    settings_url = event_url(hackathon, 'settings')
    for name, label, may_precede_start in DEADLINE_ORDER:
        value = getattr(hackathon, name)
        if value is None:
            continue
        if previous and value < previous:
            drift.append(
                {
                    'label': label,
                    'detail': f'早于「{previous_label}」，选手会先遇到后面的截止。',
                    'url': settings_url,
                    'url_label': '改活动时间',
                }
            )
        elif end and value > end:
            drift.append(
                {'label': label, 'detail': '晚于活动结束时间。', 'url': settings_url, 'url_label': '改活动时间'}
            )
        elif start and not may_precede_start and value < start:
            drift.append(
                {'label': label, 'detail': '早于活动开始时间。', 'url': settings_url, 'url_label': '改活动时间'}
            )
        if previous is None or value > previous:
            # 报告「早于谁」时要指真正压着它的那一项，而不是上一行。
            previous, previous_label = value, label
    for item in ScheduleItem.objects.filter(hackathon=hackathon, state=ScheduleItem.State.PUBLISHED):
        if (start and item.ends_at < start) or (end and item.starts_at > end):
            drift.append(
                {
                    'label': f'日程「{item.title}」',
                    'detail': '落在新的活动区间之外。',
                    'url': event_url(hackathon, 'content-edit', kind='schedule', item_id=item.pk),
                    'url_label': '改这条日程',
                }
            )
    return drift


def publish_readiness(hackathon):
    h = hackathon
    missing_info = [label for label, value in (
        ('活动名称', h.name), ('一句话介绍', h.tagline), ('简介', h.description), ('场地', h.venue)
    ) if not (value or '').strip()]
    published_tracks = Track.published(h).count()
    draft_tracks = Track.objects.filter(hackathon=h, state=Track.State.DRAFT).count()
    required_guides = Resource.published(h).filter(required=True).count()
    judges = len(active_judge_ids(h))
    rows = results(h)
    unfinished = [row for row in rows if not row['complete']]
    ranked = [row for row in rows if row['complete']]
    drafts = sum(item['count'] for item in draft_content(h))
    drift = date_drift(h)
    track_detail = (
        f'已发布 {published_tracks} 个赛道' + (f'，另有 {draft_tracks} 个草稿未发布。' if draft_tracks else '。')
        if published_tracks
        else '还没有已发布赛道，选手无法选择方向。'
    )
    return [
        {
            'label': '基本信息',
            'ready': not missing_info,
            'detail': '已填写名称、介绍、简介与场地。' if not missing_info else '尚未填写：' + '、'.join(missing_info),
            'url': event_url(h, 'settings'),
        },
        {
            'label': '时间与截止',
            'ready': not drift,
            'detail': (
                '活动起止与各项截止、日程互相一致。'
                if not drift
                else f'{len(drift)} 项与新的活动区间不一致，请逐条处理。'
            ),
            'items': drift,
            'url': event_url(h, 'settings'),
        },
        {
            'label': '赛道',
            'ready': bool(published_tracks),
            'detail': track_detail,
            'url': event_url(h, 'content'),
        },
        {
            'label': '必读材料',
            'ready': bool(required_guides),
            'detail': (
                f'已发布 {required_guides} 篇必读教程/指南。' if required_guides else '还没有标记为必读且已发布的教程。'
            ),
            'url': event_url(h, 'content'),
        },
        {
            'label': '评委名单',
            'ready': bool(judges),
            'detail': f'已配置 {judges} 位可用评委。' if judges else '还没有可用评委，开放评分会被前置条件拦住。',
            'url': event_url(h, 'staff'),
        },
        {
            'label': '成绩完整性',
            'ready': not unfinished,
            'detail': (
                f'{len(ranked)} 份作品已收齐有效评分，公布时公开名次与评语。'
                if not unfinished
                else f'{len(unfinished)} 份作品尚未收齐有效评委评分，公布成绩会被拦住。'
            ),
            'items': [
                {'label': row['project'].title, 'detail': f' · 已收 {row["count"]}/{row["expected"]} 位'}
                for row in unfinished[:6]
            ],
            'url': event_url(h, 'results'),
        },
        {
            'label': '内容草稿',
            'ready': not drafts,
            'detail': f'还有 {drafts} 条内容仍是草稿，发布后不会公开。' if drafts else '没有待发布的草稿内容。',
            'url': event_url(h, 'content'),
        },
    ]
