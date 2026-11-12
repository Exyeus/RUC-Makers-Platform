import hashlib
import secrets
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from defusedcsv import csv
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_safe
from django_scopes import scope

from eventyay.hackathon import forms, services
from eventyay.hackathon.archive import zip_preview
from eventyay.hackathon.calendar import absence_summary, schedule_heatmap, schedule_timeline
from eventyay.hackathon.community import square_context
from eventyay.hackathon.constants import KEYWORD_CATEGORIES, KEYWORDS
from eventyay.hackathon.downloads import asset_response, stream_file
from eventyay.hackathon.matching import excluded_ids
from eventyay.hackathon.models import (
    Announcement,
    Asset,
    AuditEntry,
    BonusRule,
    DemoSlot,
    Hackathon,
    JoinRequest,
    Participant,
    Project,
    Publishable,
    Resource,
    ResourceRead,
    ScheduleItem,
    Score,
    StaffAssignment,
    StationToken,
    SupportTicket,
    Team,
    TicketMessage,
    Track,
    UploadSession,
    VolunteerTask,
)
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.scoring import rule_description
from eventyay.hackathon.team_showcase import COMMUNITY_EXAMPLES, POOL_EXAMPLES, SEEKER_EXAMPLES
from eventyay.hackathon.workbench import next_task


def url(hackathon, name, **kwargs):
    return services.event_url(hackathon, name, **kwargs)


def list_filters(source, state_key='state', reason_key='reason'):
    """作品检查列表的筛选值，只认枚举里有的取值。"""
    state = source.get(state_key, '')
    reason = source.get(reason_key, '')
    return (
        state if state in Project.State.values else '',
        reason if reason in Project.StateReason.values else '',
    )


def submissions_return(hackathon, post):
    """检查完一条作品后回到原来那份筛选结果。

    跳转地址只由白名单取值拼成，不会把提交上来的任意字符串带进 URL。
    """
    state, reason = list_filters(post, 'filter_state', 'filter_reason')
    params = {}
    if state:
        params['state'] = state
    if reason:
        params['reason'] = reason
    target = url(hackathon, 'submissions')
    return f'{target}?{urlencode(params)}' if params else target


ORGANIZER_ROUTES = {
    'manage', 'participants', 'submissions', 'manage-stage', 'publish', 'content', 'staff', 'settings',
    'scoring-rules', 'matching-operations', 'support-reply',
}
JUDGE_ROUTES = {'judging', 'judge-live', 'score'}
VOLUNTEER_ROUTES = {'tasks'}
# 详情页沿用所属栏目：这些路由的侧栏入口是「我的队伍」。
TEAM_ROUTES = {
    'matching', 'matching-profile', 'idea-create', 'idea-edit', 'proposal-create', 'matching-receipt',
    'post-new', 'post-edit', 'post-detail', 'team-edit',
}
# 别名只决定侧栏高亮哪一栏，不再决定工作身份与浏览器标题。
NAVIGATION_ALIASES = {
    'register': 'home',
    'resource': 'resources',
    'project-detail': 'gallery',
    'support-reply': 'support',
    'review': 'submissions',
    'intake': 'submissions',
    'content-new': 'content',
    'content-edit': 'content',
    'content-preview': 'content',
    'judge-new': 'staff',
    'task-new': 'tasks',
    'task-edit': 'tasks',
}


def navigation_column(route, follow_live=False):
    """The sidebar entry a route belongs to; detail pages keep their column."""
    if route in TEAM_ROUTES:
        return 'teams'
    if route == 'score':
        return 'judge-live' if follow_live else 'judging'
    return NAVIGATION_ALIASES.get(route, route)


def working_identity(request, route, column):
    """Which workbench the current page belongs to, for an account holding several roles.

    The page's own route decides first: an organizer-only detail page keeps the
    organizer workbench even though the sidebar highlights a shared column.
    """
    if not request.user.is_authenticated:
        return None
    organizer = request.hackathon_organizer
    judge = 'judge' in request.hackathon_roles
    for candidate in (route, column):
        if candidate in ORGANIZER_ROUTES and organizer:
            return 'organizer'
        if candidate in JUDGE_ROUTES and judge:
            return 'judge'
        if candidate in VOLUNTEER_ROUTES and (organizer or 'volunteer' in request.hackathon_roles):
            return 'volunteer'
    if route == 'results':
        return 'organizer' if organizer else 'judge' if judge else 'participant'
    return 'participant'


def workspace_navigation(request, hackathon, identity):
    """Sidebar for the working identity: about six primary entries, the rest below.

    Every page the identity can open keeps an entry in this sidebar, so no page
    loses its `aria-current` marker; only the prominence changes. The 加分页 is
    the one deliberate exception: it stays outside the navigation (see
    doc/ui-workbench.md).
    """
    activity = [('schedule', '活动日程', 'calendar'), ('announcements', '通知公告', 'bell')]
    reference = [('resources', '学习与指南', 'book'), ('gallery', '作品展厅', 'spark')]
    help_entry = ('support', '帮助与支持', 'help')
    contestant_entries = [('teams', '我的队伍', 'users'), ('project', '我的作品', 'folder')]
    stage_entry = ('stage', '路演现场', 'play')
    judge_entries = [('judging', '评委工作台', 'chart'), ('judge-live', '当前路演', 'play')]
    results_entry = ('results', '评审与成绩', 'chart')
    if identity == 'organizer':
        groups = [
            ('我的待办', [
                ('manage', '活动总控', 'grid'),
                ('participants', '报名与签到', 'users'),
                ('submissions', '作品检查', 'folder'),
                ('manage-stage', '路演调度', 'play'),
                results_entry,
                ('publish', '发布准备', 'spark'),
            ]),
            ('配置与成员', [
                ('content', '内容管理', 'book'),
                ('staff', '成员与权限', 'badge'),
                ('settings', '活动设置', 'settings'),
                ('scoring-rules', '算分规则', 'settings'),
                ('matching-operations', '组队撮合', 'users'),
            ]),
        ]
        elsewhere = [
            ('tasks', '志愿者工作台', 'badge'),
            help_entry,
            ('home', '参赛总览', 'grid'),
            *contestant_entries,
            stage_entry,
            *activity,
            *reference,
        ]
        if 'judge' in request.hackathon_roles:
            elsewhere = judge_entries + elsewhere
        groups.append(('协作与活动页面', elsewhere))
    elif identity == 'judge':
        groups = [
            ('我的评审', [*judge_entries, results_entry]),
            ('活动与帮助', [*activity, help_entry]),
            ('参考', [*reference, stage_entry, *contestant_entries, ('home', '参赛总览', 'grid')]),
        ]
    elif identity == 'volunteer':
        groups = [
            ('我的工作', [('tasks', '志愿者工作台', 'badge'), *activity, help_entry]),
            ('参考', [*reference, stage_entry, *contestant_entries, results_entry, ('home', '参赛总览', 'grid')]),
        ]
    elif identity == 'participant':
        groups = [
            ('我的参赛', [
                ('home', '参赛总览', 'grid'),
                *contestant_entries,
            ]),
            ('活动与帮助', [*activity, help_entry]),
            ('学习与展示', [*reference, stage_entry, results_entry]),
        ]
    else:
        groups = [
            ('活动', [('home', '活动主页', 'grid'), *activity, *reference, results_entry]),
            ('入口', [
                stage_entry,
                ('teams', '组队广场', 'users'),
                help_entry,
                ('judge-login', '评委入口', 'chart'),
            ]),
        ]
    return [
        (label, [(name, title, icon, url(hackathon, name)) for name, title, icon in items])
        for label, items in groups
    ]


def working_identities(request, hackathon, identity):
    """The identities this account actually holds, for an explicit context switcher.

    Switching only changes which workbench the person is looking at; `event_view`
    still decides every permission on the destination page.
    """
    if not request.user.is_authenticated:
        return []
    identities = [('participant', '选手', 'home')]
    if 'judge' in request.hackathon_roles:
        identities.append(('judge', '评委', 'judging'))
    if request.hackathon_organizer or 'volunteer' in request.hackathon_roles:
        identities.append(('volunteer', '志愿者', 'tasks'))
    if request.hackathon_organizer:
        identities.append(('organizer', '主办', 'manage'))
    if len(identities) < 2:
        return []
    return [
        {'key': key, 'label': label, 'url': url(hackathon, route), 'current': key == identity}
        for key, label, route in identities
    ]


def page(request, template, **context):
    hackathon = request.hackathon
    participant = request.participant
    route = request.resolver_match.url_name
    common = {
        'hackathon': hackathon,
        'participant': participant,
        'is_organizer': request.hackathon_organizer,
        'is_staff_member': request.hackathon_organizer or bool(request.hackathon_roles),
        'is_judge': 'judge' in request.hackathon_roles,
        'now': timezone.now(),
        'active': route,
        'latest_notice': Announcement.published(hackathon).filter(audience='all').first(),
    }
    common.update(context)
    if common['active'] in TEAM_ROUTES:
        common['active'] = 'teams'
    common['navigation_active'] = navigation_column(route, common.get('follow_live'))
    identity = working_identity(request, route, common['navigation_active'])
    navigation_groups = workspace_navigation(request, hackathon, identity)
    common['navigation'] = [item for label, items in navigation_groups for item in items]
    common['navigation_groups'] = navigation_groups
    common['identities'] = working_identities(request, hackathon, identity)
    common['working_identity_key'] = identity
    common['working_identity'] = next((item['label'] for item in common['identities'] if item['current']), None)
    # 浏览器标题用这一页自己的任务名：视图给出的标题优先，别名只决定侧栏高亮。
    common['page_title'] = (
        common.get('page_title')
        or common.get('title')
        or next((item[1] for item in common['navigation'] if item[0] == common['navigation_active']), None)
    )
    return render(request, f'hackathon/{template}.html', common)


def report_validation(request, error):
    for message in error.messages:
        messages.error(request, message)


def legacy_workspace(request, suffix=''):
    target = reverse('hackathon:home', kwargs={'hackathon_slug': 'tongzhou26'}) + suffix
    if query := request.META.get('QUERY_STRING'):
        target += '?' + query
    response = HttpResponsePermanentRedirect(target, preserve_request=request.method not in {'GET', 'HEAD'})
    response['Cache-Control'] = 'private, no-store'
    return response


@require_GET
def index(request):
    editions = list(
        Hackathon.objects.filter(is_public=True, program=Hackathon.Program.MAKER_CAMP).select_related('event')
    )
    if len(editions) == 1:
        return redirect(url(editions[0], 'home'))
    return render(request, 'hackathon/index.html', {'editions': editions})


@event_view()
@require_GET
def home(request):
    authenticated = request.user.is_authenticated
    context = landing_context(request)
    if not authenticated:
        context['login_form'] = forms.StudentLoginForm()
        context['password_reset_url'] = url(request.hackathon, 'student-password-reset-request')
    return page(request, 'home' if authenticated else 'landing', **context)


def landing_context(request):
    h = request.hackathon
    participant = request.participant
    team = participant.team if participant else None
    project = Project.objects.filter(hackathon=h, team=team).first() if team else None
    status = services.project_status(project)
    required = Resource.published(h).filter(required=True)
    read_count = (
        ResourceRead.objects.filter(resource__in=required, user=request.user).count()
        if request.user.is_authenticated
        else 0
    )
    required_count = required.count()
    unread = None
    if request.user.is_authenticated:
        unread = required.exclude(
            pk__in=ResourceRead.objects.filter(hackathon=h, user=request.user).values('resource_id')
        ).first()
    waiting_ticket = (
        SupportTicket.objects.filter(
            hackathon=h, user=request.user, state=SupportTicket.State.WAITING_USER
        )
        .order_by('-updated')
        .first()
        if request.user.is_authenticated and not h.results_published
        else None
    )
    current_task = next_task(h, participant, project, unread, timezone.now(), status, waiting_ticket)
    checklist = [
        {
            'title': '完善参赛信息',
            'detail': '留下你的技能、联系方式与特殊时段',
            'done': bool(participant),
            'url': url(h, 'register'),
        },
        {
            'title': '找到你的队友',
            'detail': f'每队最多 {h.team_size} 人，参赛人数以活动规则为准',
            'done': bool(team),
            'url': url(h, 'teams'),
        },
        {
            'title': '读完参赛必读',
            'detail': f'已完成 {read_count} / {required_count} 篇',
            'done': read_count == required_count,
            'url': url(h, 'resources'),
        },
        {
            'title': '提交你的作品',
            'detail': status['detail'],
            'done': status['done'],
            'url': url(h, 'project'),
        },
    ]
    return {
        'team': team,
        'project': project,
        'project_status': status,
        'journey_url': url(h, 'project' if participant else 'register'),
        'current_task': current_task,
        'waiting_ticket': waiting_ticket,
        'completed_steps': [item for item in checklist if item['done']],
        'pending_steps': [item for item in checklist if not item['done']],
        'required_count': required_count,
        'read_count': read_count,
        'checklist': checklist,
        'progress': sum(item['done'] for item in checklist) * 25,
        'tracks': Track.published(h),
        'participant_count': Participant.objects.filter(hackathon=h).count(),
        'team_count': Team.objects.filter(hackathon=h).count(),
        'project_count': Project.objects.filter(
            hackathon=h, state__in=[Project.State.SUBMITTED, Project.State.APPROVED]
        ).count(),
        'notices': Announcement.published(h).filter(audience='all')[:3],
        'upcoming': ScheduleItem.published(h).filter(ends_at__gte=timezone.now())[:3],
        'featured_resources': Resource.published(h)[:3],
        'calendar_days': schedule_heatmap(h),
        'calendar_timeline': schedule_timeline(h),
    }


@event_view('login')
def register(request):
    instance = request.participant or Participant(hackathon=request.hackathon, user=request.user)
    form = forms.ParticipantForm(request.POST or None, instance=instance, hackathon=request.hackathon)
    if request.method == 'POST' and form.is_valid():
        bound_email = request.user.email
        try:
            services.save_participant(request.hackathon, request.user, form)
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error('email', '该邮箱刚刚被其他账户绑定，请更换后重试。')
        else:
            if request.user.email != bound_email:
                # The session auth hash covers the email; refresh it so saving
                # does not log the participant out of this session.
                update_session_auth_hash(request, request.user)
            messages.success(request, '参赛信息已保存。你的选手编号将贯穿本次活动。')
            return redirect(url(request.hackathon, 'home'))
    return page(
        request,
        'form',
        form=form,
        title='个人资料 · 让队友认识你',
        subtitle='选择你的关键词，用一句话介绍你想做什么。开启公开展示后，其他选手可在组队广场找到你。学号与联系方式不会公开。',
        button='保存参赛信息',
    )


@event_view()
@require_GET
def teams(request):
    h = request.hackathon
    query = request.GET.get('q', '').strip()
    teams_qs = (
        Team.objects.filter(hackathon=h, recruiting=True)
        .select_related('track')
        .annotate(member_count=Count('members'))
    )
    if query:
        teams_qs = teams_qs.filter(Q(name__icontains=query) | Q(needs__icontains=query) | Q(intro__icontains=query))
    track = request.GET.get('track', '')
    if track.isdigit():
        teams_qs = teams_qs.filter(track_id=int(track))
    own = request.participant.team if request.participant else None
    seekers = Participant.objects.filter(hackathon=h, looking_for_team=True, team__isnull=True)
    if h.matching_enabled:
        teams_qs = teams_qs.filter(state='recruiting', captain__matching_profile__opt_in=True).exclude(
            captain__matching_profile__visibility='paused'
        )
        seekers = Participant.objects.filter(
            hackathon=h, team__isnull=True, matching_profile__opt_in=True, matching_profile__visibility='event'
        )
        if request.participant:
            excluded = excluded_ids(h, request.participant)
            teams_qs = teams_qs.exclude(members__pk__in=excluded)
            seekers = seekers.exclude(pk__in=excluded)
        else:
            teams_qs, seekers = teams_qs.none(), seekers.none()
    keyword = request.GET.get('keyword', '')
    if keyword:
        seekers = seekers.filter(keywords__contains=[keyword])
    if query:
        seekers = seekers.filter(
            Q(name__icontains=query)
            | Q(tagline__icontains=query)
            | Q(skills__icontains=query)
            | Q(bio__icontains=query)
            | Q(keywords__icontains=query)
        )
    keyword_categories = KEYWORD_CATEGORIES
    if keyword and keyword not in KEYWORDS:
        keyword_categories += (('自定义关键词', (keyword,)),)
    applications = []
    if own and own.captain_id == request.participant.pk:
        applications = JoinRequest.objects.filter(team=own, state='pending').select_related('participant')
    return page(
        request,
        'teams',
        teams=teams_qs,
        own_team=own,
        applications=applications,
        own_members=own.members.all() if own else [],
        query=query,
        selected_track=track,
        tracks=Track.published(h),
        seekers=seekers.order_by('-updated')[:60],
        keyword_categories=keyword_categories,
        selected_keyword=keyword,
        seeker_examples=SEEKER_EXAMPLES,
        community_examples=COMMUNITY_EXAMPLES,
        pool_examples=POOL_EXAMPLES,
        seeker_profile_url=url(h, 'register'),
        **square_context(h, request.participant, request.GET),
    )


@event_view('login')
def team_edit(request):
    if request.hackathon.matching_enabled and not request.participant:
        return redirect(url(request.hackathon, 'register'))
    if request.hackathon.matching_enabled and not request.participant.team_id:
        return redirect(url(request.hackathon, 'proposal-create'))
    participant = request.participant
    if not participant:
        return redirect(url(request.hackathon, 'register'))
    team = participant.team
    if team and team.captain_id != participant.pk:
        raise PermissionDenied
    form = forms.TeamForm(request.POST or None, instance=team, hackathon=request.hackathon)
    if request.method == 'POST' and form.is_valid():
        try:
            services.save_team(request.hackathon, participant, form)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, '队伍已保存，邀请队友一起把想法做出来。')
            return redirect(url(request.hackathon, 'teams'))
    return page(
        request,
        'form',
        form=form,
        title='管理我的队伍' if team else '发起一支队伍',
        subtitle='队伍冻结前可以调整成员；公开招募不会公开联系方式。',
        button='保存队伍',
    )


@event_view('login')
@require_POST
def team_action(request):
    h, participant = request.hackathon, request.participant
    if not participant:
        return redirect(url(h, 'register'))
    action = request.POST.get('action')
    if h.matching_enabled:
        return redirect(url(h, 'matching'))
    try:
        with transaction.atomic():
            h = services.lock_event(h)
            services.require_team_open(h)
            if action == 'leave':
                services.leave_team(h, participant)
            elif action == 'invite':
                team = get_object_or_404(Team, hackathon=h, invite_code=request.POST.get('code', '').strip())
                services.join_team(h, participant, team, invite_code=team.invite_code)
            elif action == 'apply':
                if participant.team_id:
                    raise ValidationError('你已在队伍中，无法申请另一支队伍。')
                team = get_object_or_404(Team, hackathon=h, pk=request.POST.get('team'), recruiting=True)
                message = request.POST.get('message', '').strip()
                if not message or len(message) > 300:
                    raise ValidationError('请填写 1–300 字的申请说明。')
                JoinRequest.objects.update_or_create(
                    hackathon=h, participant=participant, team=team, defaults={'message': message, 'state': 'pending'}
                )
            elif action in {'accept', 'decline'}:
                application = get_object_or_404(
                    JoinRequest.objects.select_related('team', 'participant'),
                    hackathon=h,
                    pk=request.POST.get('application'),
                    state='pending',
                )
                if application.team.captain_id != participant.pk:
                    raise PermissionDenied
                if action == 'accept':
                    services.join_team(h, application.participant, application.team, application=application)
                else:
                    application.state = 'declined'
                    application.save(update_fields=['state', 'updated'])
            elif action == 'transfer':
                team = get_object_or_404(Team, hackathon=h, captain=participant)
                member = get_object_or_404(Participant, hackathon=h, team=team, pk=request.POST.get('member'))
                team.captain = member
                team.roster_version += 1
                team.save(update_fields=['captain', 'roster_version', 'updated'])
                services.audit(h, request.user, '转交队长', team.code, member.code)
            elif action == 'rotate':
                team = get_object_or_404(Team, hackathon=h, captain=participant)
                team.invite_code = secrets.token_urlsafe(12)
                team.save(update_fields=['invite_code', 'updated'])
            else:
                raise ValidationError('未知组队操作。')
        messages.success(request, '组队操作已完成。')
    except ValidationError as error:
        report_validation(request, error)
    return redirect(url(h, 'teams'))


@event_view('login')
def project(request):
    h, participant = request.hackathon, request.participant
    own = (
        Project.objects.filter(team=participant.team).prefetch_related('assets').first()
        if participant and participant.team_id
        else None
    )
    if not participant or not participant.team_id:
        return page(request, 'project', project=None, project_status=services.project_status(None), needs_team=True)
    captain = participant.team.captain_id == participant.pk
    form = forms.ProjectForm(request.POST or None, instance=own)
    if request.method == 'POST':
        if not captain:
            raise PermissionDenied
        if form.is_valid():
            try:
                saved = services.save_project(h, participant, form, submit=request.POST.get('action') == 'submit')
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if request.headers.get('Accept') == 'application/json':
                    return JsonResponse(project_state_data(saved))
                messages.success(
                    request,
                    '提交意图已登记，材料上传完成后自动正式提交。'
                    if saved.state == 'queued'
                    else '作品已正式提交。'
                    if request.POST.get('action') == 'submit'
                    else '草稿已保存。',
                )
                return redirect(url(h, 'project'))
        if request.headers.get('Accept') == 'application/json':
            fields = {
                name: str(errors[0]) for name, errors in form.errors.items() if name != '__all__' and len(errors)
            }
            headline = ' '.join(str(message) for message in form.errors.get('__all__', []))
            return JsonResponse(
                {'error': headline or '部分信息未通过校验，请查看表单中标出的字段。', 'fields': fields},
                status=400,
            )
    return page(
        request,
        'project',
        project=own,
        project_status=services.project_status(own),
        form=form,
        captain=captain,
        asset_form=forms.AssetForm(),
        active_uploads=UploadSession.objects.filter(project=own, state='uploading', expires_at__gt=timezone.now())
        if own
        else [],
        upload_history=UploadSession.objects.filter(project=own).order_by('-created')[:30] if own else [],
        submission_errors=services.submission_errors(own) if own else [],
        feedback=Score.objects.filter(project=own, final=True).exclude(feedback='')
        if own and h.results_published
        else [],
    )


def project_state_data(project):
    tasks = list(project.uploads.filter(state='uploading'))
    status = services.project_status(project, uploads=tasks)
    data = {
        'state': project.state,
        'label': status['label'],
        'revision': project.revision,
        'receipt_id': str(project.submission_receipt) if project.submission_receipt else None,
        'received_at': project.submission_intent_at.isoformat() if project.submission_intent_at else None,
        'client_clicked_at': project.submission_client_clicked_at.isoformat()
        if project.submission_client_clicked_at
        else None,
        'project_id': str(project.pk),
        'submitted_at': project.submitted_at.isoformat() if project.submitted_at else None,
        'manifest': project.submission_manifest,
        'manifest_sha256': project.submission_manifest_hash,
    }
    data['receipt'] = signing.dumps(data, salt='hackathon.submission-receipt', compress=True)
    # Status copy is for the live badge only; the signed receipt payload above stays frozen.
    data['status_key'] = status['key']
    data['status_detail'] = status['detail']
    data['status_action'] = status['action']
    data['status_tone'] = status['tone']
    data['materials'] = [
        {
            'kind': asset.get_kind_display(),
            'filename': asset.filename,
            'size_display': filesizeformat(asset.size),
            'download_url': url(project.hackathon, 'download', asset_id=asset.pk),
        }
        for asset in project.assets.all()
    ]
    data['gaps'] = services.submission_errors(project)
    return data


@event_view('login')
@require_GET
def project_status(request):
    if not request.participant or not request.participant.team_id:
        raise Http404
    item = get_object_or_404(Project, hackathon=request.hackathon, team=request.participant.team)
    response = JsonResponse(project_state_data(item))
    response['Cache-Control'] = 'no-store'
    if request.GET.get('download') == '1':
        response['Content-Disposition'] = f'attachment; filename="submission-{item.pk}.json"'
    return response


@event_view('login')
@require_POST
@transaction.atomic
def cancel_submission_queue(request):
    services.lock_event(request.hackathon)
    if (
        not request.participant
        or not request.participant.team_id
        or (request.participant.team.captain_id != request.participant.pk)
    ):
        raise PermissionDenied
    item = get_object_or_404(Project.objects.select_for_update(), team=request.participant.team)
    if item.state == 'queued':
        item.state = Project.State.CHANGES
        item.state_reason = Project.StateReason.UPLOAD
        item.save(update_fields=['state', 'state_reason', 'updated'])
        services.audit(request.hackathon, request.user, '取消排队提交', item.code, str(item.submission_receipt))
    return JsonResponse(project_state_data(item))


@event_view('login')
@require_POST
def upload(request):
    participant = request.participant
    if not participant or not participant.team_id:
        raise PermissionDenied
    own = get_object_or_404(Project, hackathon=request.hackathon, team=participant.team)
    form = forms.AssetForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            services.save_asset(request.hackathon, participant, own, form)
        except ValidationError as error:
            report_validation(request, error)
        else:
            messages.success(request, '材料已上传。检查所有材料后，请再次点击“正式提交”。')
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect(url(request.hackathon, 'project'))


def can_read_project(request, project):
    return (
        request.hackathon_organizer
        or 'judge' in request.hackathon_roles
        or (request.participant and request.participant.team_id == project.team_id)
        or (request.hackathon.gallery_open and project.public_consent and project.state == 'approved')
    )


@event_view()
@require_GET
def project_detail(request, project_id):
    item = get_object_or_404(Project.objects.select_related('team__track'), hackathon=request.hackathon, pk=project_id)
    if not can_read_project(request, item):
        raise Http404
    return page(
        request,
        'project_detail',
        project=item,
        title=item.title,
        assets=item.assets.all()
        if request.hackathon_organizer
        or 'judge' in request.hackathon_roles
        or (request.participant and request.participant.team_id == item.team_id)
        else item.assets.filter(kind='poster'),
    )


@event_view()
@require_safe
def download(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related('project__team'), pk=asset_id, hackathon=request.hackathon)
    owner = request.participant and request.participant.team_id == asset.project.team_id
    if not (
        request.hackathon_organizer
        or 'judge' in request.hackathon_roles
        or owner
        or (asset.kind == 'poster' and can_read_project(request, asset.project))
    ):
        raise Http404
    return asset_response(request, asset, attachment=asset.kind != 'poster')


@event_view('login')
@require_GET
def asset_preview(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related('project__team'), pk=asset_id, hackathon=request.hackathon)
    owner = request.participant and request.participant.team_id == asset.project.team_id
    if not (owner or request.hackathon_organizer or 'judge' in request.hackathon_roles):
        raise Http404
    if not asset.original_filename.lower().endswith('.zip'):
        return JsonResponse({'error': '目前仅支持 ZIP 一级目录预览。'}, status=400)
    try:
        with asset.file.open('rb') as source:
            data = zip_preview(source)
    except (OSError, zipfile.BadZipFile, UnicodeError, ValidationError):
        return JsonResponse({'error': '无法读取压缩包目录，请联系技术人员核查。'}, status=422)
    response = JsonResponse({'filename': asset.original_filename, 'sha256': asset.checksum, **data})
    response['Cache-Control'] = 'no-store'
    return response


@event_view()
@require_GET
def resources(request):
    items = Resource.published(request.hackathon)
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '')
    if query:
        items = items.filter(Q(title__icontains=query) | Q(summary__icontains=query) | Q(body__icontains=query))
    if category:
        items = items.filter(category=category)
    read_ids = (
        set(
            ResourceRead.objects.filter(hackathon=request.hackathon, user=request.user).values_list(
                'resource_id', flat=True
            )
        )
        if request.user.is_authenticated
        else set()
    )
    return page(
        request,
        'resources',
        resources=items,
        query=query,
        category=category,
        categories=Resource.Category.choices,
        read_ids=read_ids,
    )


@event_view()
def resource_detail(request, resource_id):
    item = get_object_or_404(Resource.published(request.hackathon), pk=resource_id)
    read = request.user.is_authenticated and ResourceRead.objects.filter(resource=item, user=request.user).exists()
    if request.method == 'POST':
        if not request.user.is_authenticated:
            raise PermissionDenied
        ResourceRead.objects.get_or_create(hackathon=request.hackathon, resource=item, user=request.user)
        messages.success(request, '已记录阅读进度。')
        return redirect(url(request.hackathon, 'resource', resource_id=item.pk))
    return page(request, 'resource', resource=item, read=read, title=item.title)


@event_view()
@require_GET
def schedule(request):
    return page(
        request,
        'schedule',
        items=ScheduleItem.published(request.hackathon),
        calendar_days=schedule_heatmap(request.hackathon),
    )


@event_view()
@require_GET
def announcements(request):
    items = Announcement.published(request.hackathon)
    if not request.hackathon_organizer and not request.hackathon_roles:
        items = items.filter(audience='all')
    return page(request, 'announcements', notices=items)


@event_view()
@require_GET
def gallery(request):
    h = request.hackathon
    items = (
        Project.objects.filter(hackathon=h, public_consent=True, state='approved')
        .select_related('team__track')
        .prefetch_related('assets')
        if h.gallery_open
        else Project.objects.none()
    )
    query = request.GET.get('q', '').strip()
    if query:
        items = items.filter(Q(title__icontains=query) | Q(summary__icontains=query) | Q(team__name__icontains=query))
    return page(
        request,
        'gallery',
        projects=items,
        query=query,
        editions=Hackathon.objects.filter(
            is_public=True, gallery_open=True, program=Hackathon.Program.MAKER_CAMP
        ).exclude(pk=h.pk),
    )


@event_view('organizer')
@require_GET
def manage(request):
    h = request.hackathon
    special = list(
        Participant.objects.filter(hackathon=h).exclude(availability='', unavailable_slots=[]).select_related('team')
    )
    for person in special:
        person.attendance_summary = absence_summary(h, person.unavailable_slots, person.availability)
    projects = Project.objects.filter(hackathon=h)
    submissions_url = url(h, 'submissions')
    changes_url = f'{submissions_url}?state={Project.State.CHANGES}'
    attention = [('排队上传中', projects.filter(state=Project.State.QUEUED), f'{submissions_url}?state=queued')]
    for label, reason in (
        ('改了没重交', Project.StateReason.EDITED),
        ('上传中止未提交', Project.StateReason.UPLOAD),
        ('主办方要求补充', Project.StateReason.REVIEW),
    ):
        bucket = projects.filter(state=Project.State.CHANGES, state_reason=reason)
        attention.append((label, bucket, f'{changes_url}&reason={reason}'))
    attention = [(label, bucket.count(), link) for label, bucket, link in attention]
    return page(
        request,
        'manage',
        participant_count=Participant.objects.filter(hackathon=h).count(),
        checked_in=Participant.objects.filter(hackathon=h, checked_in=True).count(),
        team_count=Team.objects.filter(hackathon=h).count(),
        submitted=projects.filter(state__in=[Project.State.SUBMITTED, Project.State.APPROVED]).count(),
        pending=projects.filter(state='submitted').count(),
        missing=Team.objects.filter(hackathon=h).filter(
            Q(project__isnull=True) | Q(project__submitted_at__isnull=True)
        ),
        attention=attention,
        special=special,
        tickets=SupportTicket.objects.filter(hackathon=h, state=SupportTicket.State.OPEN).count(),
        waiting_tickets=SupportTicket.objects.filter(hackathon=h, state=SupportTicket.State.WAITING_USER).count(),
        log=AuditEntry.objects.filter(hackathon=h).select_related('actor')[:12],
    )


@event_view('organizer')
def participants(request):
    h = request.hackathon
    query = request.GET.get('q', '').strip()
    if request.method == 'POST':
        # 表单没有 action，POST 仍带 ?q=…，回到列表时不必重新搜索一次。
        back = f"{url(h, 'participants')}?{request.GET.urlencode()}"
        person = get_object_or_404(Participant, hackathon=h, pk=request.POST.get('participant'))
        action = request.POST.get('action')
        if action == 'check_in':
            if person.checked_in:
                messages.info(request, f'{person.name}（{person.code}）此前已签到，状态没有变化。')
            else:
                person.checked_in = True
                person.save(update_fields=['checked_in', 'updated'])
                services.audit(h, request.user, '签到', person.code)
                messages.success(request, f'{person.name}（{person.code}）已签到。')
        elif action == 'uncheck':
            if not person.checked_in:
                messages.info(request, f'{person.name}（{person.code}）本来就没有签到记录。')
            else:
                person.checked_in = False
                person.save(update_fields=['checked_in', 'updated'])
                services.audit(h, request.user, '撤销签到', person.code)
                messages.success(request, f'已撤销 {person.name}（{person.code}）的签到记录。')
        else:
            messages.error(request, '没有识别这个操作，签到状态保持不变。')
        return redirect(back)
    items = (
        Participant.objects.filter(hackathon=h)
        .select_related('team', 'user')
        .annotate(student_id=F('user__student_account__student_id'))
    )
    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(school__icontains=query)
            | Q(team__name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__student_account__student_id__icontains=query)
        )
    people = list(items)
    for person in people:
        person.attendance_summary = absence_summary(h, person.unavailable_slots, person.availability)
    return page(request, 'participants', participants=people, query=query)


@event_view('organizer')
@require_GET
def submissions(request):
    items = Project.objects.filter(hackathon=request.hackathon).select_related('team__track').prefetch_related('assets')
    state, reason = list_filters(request.GET)
    if state:
        items = items.filter(state=state)
    if reason:
        items = items.filter(state_reason=reason)
    return page(
        request,
        'submissions',
        projects=items,
        states=Project.State.choices,
        state=state,
        reasons=Project.StateReason.choices,
        reason=reason,
    )


@event_view('organizer')
def review(request, project_id):
    h = request.hackathon
    item = get_object_or_404(Project.objects.select_related('team__track'), hackathon=h, pk=project_id)
    form = forms.ReviewForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            current_hackathon = services.lock_event(h)
            current_project = Project.objects.select_for_update().get(pk=item.pk, hackathon=h)
            if current_hackathon.results_published and form.cleaned_data['state'] != current_project.state:
                form.add_error('state', '成绩已公布，不能变更参评作品状态。')
            if form.cleaned_data['state'] == 'approved':
                errors = services.submission_errors(current_project)
                if not current_project.submitted_at:
                    errors.append('队长尚未正式提交作品。')
                if errors:
                    form.add_error(None, ValidationError(errors))
            if not form.errors:
                for field in form.Meta.fields:
                    setattr(current_project, field, form.cleaned_data[field])
                current_project.state_reason = (
                    Project.StateReason.REVIEW if current_project.state == Project.State.CHANGES else ''
                )
                current_project.save(update_fields=[*form.Meta.fields, 'state_reason', 'updated'])
                if current_project.state != 'approved' or not current_project.equipment_checked:
                    DemoSlot.objects.filter(project=current_project, state='ready').update(state='waiting')
                services.audit(h, request.user, '作品检查', item.code, item.review_note)
                messages.success(request, '检查意见已保存，队伍可以在“我的作品”中查看。')
                return redirect(submissions_return(h, request.POST))
    state, reason = list_filters(request.GET)
    return page(
        request,
        'review',
        form=form,
        project=item,
        title=item.title,
        assets=item.assets.all(),
        filter_state=state,
        filter_reason=reason,
    )


@event_view('organizer')
def intake(request, team_id):
    h = request.hackathon
    team = get_object_or_404(Team.objects.select_related('captain'), hackathon=h, pk=team_id)
    item = Project.objects.filter(team=team).first()
    upload_action = request.method == 'POST' and request.POST.get('action') == 'upload'
    form = forms.IntakeForm(request.POST if request.method == 'POST' and not upload_action else None, instance=item)
    asset_form = forms.IntakeAssetForm(
        request.POST if upload_action else None, request.FILES if upload_action else None
    )
    active_form = asset_form if upload_action else form
    if request.method == 'POST' and active_form.is_valid():
        try:
            if upload_action:
                if not item:
                    raise ValidationError('请先保存作品草稿。')
                services.save_asset(
                    h, team.captain, item, asset_form, organizer=request.user, reason=asset_form.cleaned_data['reason']
                )
            else:
                services.save_project(
                    h,
                    team.captain,
                    form,
                    submit=request.POST.get('action') == 'submit',
                    organizer=request.user,
                    reason=form.cleaned_data['reason'],
                )
        except ValidationError as error:
            active_form.add_error(None, error)
        else:
            messages.success(request, '补录已保存，操作人、原因与版本已写入日志。')
            return redirect(url(h, 'intake', team_id=team.pk))
    return page(
        request,
        'intake',
        form=form,
        asset_form=asset_form,
        team=team,
        project=item,
        title=f'作品异常补录 · {team.name}',
    )


@event_view('organizer')
def settings(request):
    h = request.hackathon
    form = forms.SettingsForm(request.POST or None, instance=h)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            current = services.lock_event(h)
            if current.matching_enabled and ({'team_size', 'team_freeze_at'} & set(form.changed_data)):
                form.add_error(None, '组队广场开放后，人数政策请在组队撮合工作台修改；本轮冻结时间不能在此覆盖。')
            if not form.errors:
                form.save()
                services.audit(h, request.user, '更新活动设置', detail=', '.join(form.changed_data))
                messages.success(request, '活动设置已保存。发布开关请在「发布准备」里逐项操作。')
                if {'starts_at', 'ends_at'} & set(form.changed_data):
                    conflicts = form.date_conflicts()
                    if conflicts:
                        messages.warning(
                            request,
                            '活动起止时间已更新，以下事项与新的活动窗口不一致，请确认是否需要一并调整：'
                            + '；'.join(conflicts)
                            + '。',
                        )
                return redirect(url(h, 'settings'))
    return page(
        request,
        'form',
        form=form,
        title='活动设置',
        subtitle=(
            f'所有时间以活动时区 {h.event.timezone} 为准。修改截止时间会记录在操作日志中；'
            '开放活动、展厅、评分与公布成绩不在这一页。'
        ),
        button='保存设置',
        drift=services.date_drift(h),
    )


CONTENT = {
    'bonus': (BonusRule, forms.BonusRuleForm, '加分规则', False),
    'announcement': (Announcement, forms.AnnouncementForm, '通知', True),
    'resource': (Resource, forms.ResourceForm, '教程与指南', True),
    'schedule': (ScheduleItem, forms.ScheduleForm, '日程', True),
    'track': (Track, forms.TrackForm, '赛道', True),
}

# 每种保存方式都是页面上的一个独立按钮：新内容默认停在草稿，不点发布就不会接触选手端。
# 对已发布条目点「保存草稿」会当场下架，模板对这一步先要求确认。
# (提交的 action, 落库的 state, 按钮文案, 按钮样式, 说明)
CONTENT_ACTIONS = (
    ('draft', 'draft', '保存草稿', 'secondary', '草稿只给主办自己看，选手端列表与直达链接都不会出现。'),
    (
        'publish',
        'published',
        '发布 / 更新公开版',
        'primary',
        '保存后选手立即可见；如果已有公开版，这一步会用当前内容覆盖它。',
    ),
    (
        'archive',
        'archived',
        '归档',
        'secondary',
        '归档不再对选手公开，但记录与引用都会保留，队伍仍显示它原来选的赛道。',
    ),
)

PLAIN_ACTION = (('save', '', '保存', 'primary', ''),)

CONTENT_WORDS = {'draft': '存草稿', 'publish': '发布', 'archive': '归档', 'save': '更新'}

CONTENT_DONE = {
    'draft': '{label}草稿已保存，选手端还看不到。',
    'publish': '{label}已发布，选手现在可以看到。',
    'archive': '{label}已归档，记录保留但不再对选手公开。',
    'save': '{label}已保存。',
}

# kind -> (公开页 url name, 该页把条目放进哪个上下文变量)
CONTENT_PREVIEW = {
    'announcement': ('announcements', 'notices'),
    'resource': ('resource', 'resource'),
    'schedule': ('schedule', 'items'),
}


@event_view('organizer')
@require_GET
def content(request):
    groups = []
    for kind, (model, _form, label, live) in CONTENT.items():
        items = model.objects.filter(hackathon=request.hackathon)
        groups.append(
            {
                'kind': kind,
                'label': label,
                'items': items,
                'live': live,
                'drafts': items.filter(state=Publishable.State.DRAFT).count() if live else 0,
                'previewable': kind in CONTENT_PREVIEW,
            }
        )
    return page(request, 'content', groups=groups)


@event_view('organizer')
def content_edit(request, kind, item_id=None):
    if kind not in CONTENT:
        raise Http404
    model, form_class, label, live = CONTENT[kind]
    instance = get_object_or_404(model, hackathon=request.hackathon, pk=item_id) if item_id else None
    form = form_class(request.POST or None, instance=instance)
    actions = CONTENT_ACTIONS if live else PLAIN_ACTION
    if request.method == 'POST':
        token = request.POST.get('action')
        chosen = next((row for row in actions if row[0] == token), None)
        if chosen is None:
            messages.error(request, '没有收到明确的保存方式，本次未保存任何内容。')
        elif form.is_valid():
            item = form.save(commit=False)
            item.hackathon = request.hackathon
            if live:
                item.state = chosen[1]
                if token == 'publish':
                    item.published_at = timezone.now()
            item.save()
            services.audit(request.hackathon, request.user, f'{CONTENT_WORDS[token]}{label}', item.pk)
            messages.success(request, CONTENT_DONE[token].format(label=label))
            return redirect(url(request.hackathon, 'content'))
    return page(
        request,
        'form',
        form=form,
        title=f'编辑{label}' if instance else f'新增{label}',
        actions=actions,
        item=instance,
        back_url=url(request.hackathon, 'content'),
        back_label='回到内容管理',
        state_label=instance.get_state_display() if live and instance else '',
        published_at=instance.published_at if live and instance else None,
        preview_url=(
            url(request.hackathon, 'content-preview', kind=kind, item_id=instance.pk)
            if live and instance and kind in CONTENT_PREVIEW
            else ''
        ),
    )


@event_view('organizer')
@require_GET
def content_preview(request, kind, item_id):
    """The real contestant page, with this one item shown as if published: read-only, nothing is written."""
    if kind not in CONTENT_PREVIEW:
        raise Http404
    model, _form, label, _live = CONTENT[kind]
    item = get_object_or_404(model, hackathon=request.hackathon, pk=item_id)
    h = request.hackathon
    template, key = CONTENT_PREVIEW[kind]
    note = (
        f'这条{label}当前是「{item.get_state_display()}」。页面就是选手将看到的真实样式，'
        '主办方编辑入口已隐藏；预览不保存、不发布，也不记录阅读进度。'
    )
    if kind == 'announcement' and item.audience != 'all':
        note += '这条通知只发给工作人员，选手端公告列表本来不会出现它，这里只预览排版。'
    context = {
        'preview': True,
        'preview_title': f'{label}预览',
        'preview_note': note,
        'is_organizer': False,
        'preview_back': url(h, 'content-edit', kind=kind, item_id=item.pk),
        'title': {
            'announcement': '通知公告',
            'resource': item.title,
            'schedule': '活动日程',
        }.get(kind, f'{label}预览'),
    }
    if kind == 'resource':
        context.update({'resource': item, 'read': False})
    else:
        context[key] = model.published(h) | model.objects.filter(pk=item.pk)
    if kind == 'schedule':
        context['calendar_days'] = schedule_heatmap(h)
    return page(request, template, **context)


@event_view('organizer')
def publish(request):
    """Four explicit publish actions; ordinary settings edits never reach any of them."""
    h = request.hackathon
    if request.method == 'POST':
        try:
            label, value = services.set_publish_flag(h, request.user, request.POST.get('action'))
        except ValidationError as error:
            report_validation(request, error)
        else:
            messages.success(request, f'{"已开启" if value else "已关闭"}「{label}」，立即生效。')
            return redirect(url(h, 'publish'))
    return page(
        request,
        'publish',
        switches=services.publish_plan(h),
        checklist=services.publish_readiness(h),
        drafts=services.draft_content(h),
    )


@event_view('organizer')
def staff(request):
    h = request.hackathon
    form = forms.StaffForm(request.POST or None)
    if request.method == 'POST':
        with transaction.atomic():
            h = services.lock_event(h)
            if h.results_published:
                messages.error(request, '成绩已公布，评委与工作人员名单已锁定。')
                return redirect(url(h, 'staff'))
            if request.POST.get('action') == 'remove':
                assignment = get_object_or_404(StaffAssignment, hackathon=h, pk=request.POST.get('assignment'))
                services.audit(h, request.user, '移除工作人员角色', assignment.user_id, assignment.role)
                assignment.delete()
                return redirect(url(h, 'staff'))
            if form.is_valid():
                StaffAssignment.objects.get_or_create(hackathon=h, user=form.staff_user, role=form.cleaned_data['role'])
                services.audit(h, request.user, '分配工作人员角色', form.staff_user.pk, form.cleaned_data['role'])
                messages.success(request, '角色已分配，对方登录后即可看到对应工作台。')
                return redirect(url(h, 'staff'))
    return page(
        request, 'staff', form=form, assignments=StaffAssignment.objects.filter(hackathon=h).select_related('user')
    )


@event_view('volunteer')
def tasks(request):
    h = request.hackathon
    items = VolunteerTask.objects.filter(hackathon=h).select_related('owner', 'backup')
    if not request.hackathon_organizer:
        items = items.filter(Q(owner=request.user) | Q(backup=request.user))
    if request.method == 'POST':
        with transaction.atomic():
            item = get_object_or_404(items.select_for_update(of=('self',)), pk=request.POST.get('task'))
            action = request.POST.get('action')
            if action == 'start' and item.state == 'todo':
                item.state, item.started_at = 'doing', timezone.now()
            elif action == 'done' and item.state == 'doing':
                item.state, item.finished_at = 'done', timezone.now()
            else:
                messages.error(request, '任务状态已变化，请刷新后再操作。')
                return redirect(url(h, 'tasks'))
            item.save()
            services.audit(h, request.user, f'志愿任务：{action}', item.pk)
        messages.success(request, '任务状态已更新，服务时间已留档，等待主办方核准。')
        return redirect(url(h, 'tasks'))
    return page(request, 'tasks', tasks=items)


@event_view('organizer')
def task_edit(request, task_id=None):
    instance = get_object_or_404(VolunteerTask, hackathon=request.hackathon, pk=task_id) if task_id else None
    form = forms.TaskForm(request.POST or None, instance=instance, hackathon=request.hackathon)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.hackathon = request.hackathon
        item.save()
        services.audit(request.hackathon, request.user, '保存志愿任务 / 核准时长', item.pk)
        return redirect(url(request.hackathon, 'tasks'))
    return page(
        request,
        'form',
        form=form,
        title='安排志愿任务',
        subtitle='明确唯一负责人、备份负责人和操作步骤。请先在工作人员中添加志愿者。',
        button='保存任务',
    )


def identity_label(person, user):
    """工单里怎么认出一个人：姓名 · 本届编号 · 队伍，没报名才退回账户姓名加账户号。

    邮箱不是唯一身份，无邮箱的学号账户也要能被找到。
    """
    if person:
        team = person.team.name if person.team_id else '尚未组队'
        return f'{person.name} · {person.code} · {team}'
    return f'{user.fullname or user.email or "未命名账户"} · 账户 #{user.pk}'


TICKET_STATE_TONES = {
    SupportTicket.State.OPEN: 'orange',
    SupportTicket.State.DOING: 'lavender',
    SupportTicket.State.WAITING_USER: '',
    SupportTicket.State.RESOLVED: 'mint',
}


def attach_ticket_threads(hackathon, tickets):
    """把开单内容和之后的每一条发言按时间排好，处理人和时间都保留。

    发言和作者在这里一次取回，调用方不必自己 prefetch，详情页也不会逐条查作者。
    """
    threads = {}
    for message in TicketMessage.objects.filter(ticket__in=tickets).select_related('author'):
        threads.setdefault(message.ticket_id, []).append(message)
    users = {ticket.user_id: ticket.user for ticket in tickets}
    for replies in threads.values():
        for message in replies:
            users[message.author_id] = message.author
    people = {
        person.user_id: person
        for person in Participant.objects.filter(hackathon=hackathon, user__in=list(users.values())).select_related(
            'team'
        )
    }
    team_ids = [person.team_id for person in people.values() if person.team_id]
    team_projects = {}
    for project in Project.objects.filter(hackathon=hackathon, team_id__in=team_ids):
        team_projects[project.team_id] = project
    for ticket in tickets:
        person = people.get(ticket.user_id)
        ticket.identity = identity_label(person, users[ticket.user_id])
        ticket.state_tone = TICKET_STATE_TONES[ticket.state]
        project = team_projects.get(person.team_id) if person and person.team_id else None
        if project:
            # 开单人属于哪一队，主办就能从工单直接走到那份作品。
            ticket.work_reference = (f'{project.code} · 当前作品', url(hackathon, 'review', project_id=project.pk))
        elif person:
            ticket.work_reference = (
                '在名单中查找本人',
                f"{url(hackathon, 'participants')}?{urlencode({'q': person.code})}",
            )
        else:
            ticket.work_reference = None
        thread = [
            {
                'at': ticket.created,
                'who': ticket.identity,
                'from_staff': False,
                'body': ticket.body,
                'is_opening': True,
            }
        ]
        for message in threads.get(ticket.pk, []):
            thread.append(
                {
                    'at': message.created,
                    'who': identity_label(people.get(message.author_id), users[message.author_id]),
                    'from_staff': message.from_staff,
                    'body': message.body,
                    'is_opening': False,
                }
            )
        ticket.thread = thread


@event_view('login')
def support(request):
    h = request.hackathon
    query_state = request.GET.get('state', '')
    state = query_state if query_state in SupportTicket.State.values else ''
    tickets = SupportTicket.objects.filter(hackathon=h)
    if not request.hackathon_organizer:
        tickets = tickets.filter(user=request.user)
    if request.hackathon_organizer and state:
        tickets = tickets.filter(state=state)
    items = list(tickets.select_related('user', 'handler'))
    attach_ticket_threads(h, items)
    back = f"{url(h, 'support')}?{urlencode({'state': state})}" if state else url(h, 'support')
    action = request.POST.get('action') if request.method == 'POST' else None
    if action == 'append':
        # 追加只认本人开出的工单，主办在同一页面看到的每一条发言都不会被覆盖。
        ticket = SupportTicket.objects.filter(hackathon=h, user=request.user, pk=request.POST.get('ticket')).first()
        if not ticket:
            messages.error(request, '这条工单不属于当前账户，没有写入任何内容。')
            return redirect(back)
        note_form = forms.TicketNoteForm(request.POST)
        if not note_form.is_valid():
            messages.error(request, '请先填写要补充的内容。')
            return redirect(back)
        with transaction.atomic():
            TicketMessage.objects.create(
                hackathon=h, ticket=ticket, author=request.user, from_staff=False, body=note_form.cleaned_data['body']
            )
            ticket.state = SupportTicket.State.OPEN
            ticket.save(update_fields=['state', 'updated'])
            services.audit(h, request.user, '追加工单说明', ticket.pk)
        messages.success(request, f'已追加到工单 #{ticket.pk}，现在轮到主办处理。')
        return redirect(back)
    form = forms.SupportForm(request.POST if action == 'new' else None)
    if action == 'new' and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.hackathon, item.user = h, request.user
            item.save()
            services.audit(h, request.user, '提交支持工单', item.pk)
        messages.success(request, f'工单 #{item.pk} 已收到，可以在本页跟踪处理结果。')
        return redirect(url(h, 'support'))
    if request.method == 'POST' and action != 'new':
        # 校验失败的新建工单仍留在本页，逐字段告诉开单人缺了什么。
        messages.error(request, '没有识别这个操作，工单内容没有被保存。')
        return redirect(back)
    return page(
        request,
        'support',
        form=form,
        tickets=items,
        states=SupportTicket.State.choices,
        state=state,
    )


@event_view('organizer')
def support_reply(request, ticket_id):
    h = request.hackathon
    item = get_object_or_404(SupportTicket.objects.select_related('user', 'handler'), hackathon=h, pk=ticket_id)
    query_state = request.GET.get('state', '')
    state = query_state if query_state in SupportTicket.State.values else ''
    back = f"{url(h, 'support')}?{urlencode({'state': state})}" if state else url(h, 'support')
    form = forms.ReplyForm(request.POST or None)
    if request.method == 'POST':
        if request.POST.get('action') != 'reply':
            messages.error(request, '没有识别这个操作，工单没有被改动。')
            return redirect(back)
        if form.is_valid():
            with transaction.atomic():
                TicketMessage.objects.create(
                    hackathon=h,
                    ticket=item,
                    author=request.user,
                    from_staff=True,
                    body=form.cleaned_data['body'],
                )
                item.state = form.cleaned_data['state']
                item.handler = request.user
                item.save(update_fields=['state', 'handler', 'updated'])
                services.audit(h, request.user, '处理支持工单', item.pk, item.get_state_display())
            messages.success(request, f'已回复工单 #{item.pk}，此前的每一轮说明都还保留。')
            return redirect(back)
    attach_ticket_threads(h, [item])
    return page(request, 'ticket', ticket=item, form=form, filter_state=state, title=item.subject)


@event_view()
@require_GET
def results(request):
    if not request.hackathon.results_published and not request.hackathon_organizer:
        return page(request, 'results', rows=[], unpublished=True)
    return page(
        request,
        'results',
        rows=services.results(request.hackathon),
        rule_description=rule_description(request.hackathon),
    )


def stage_data(hackathon):
    slots = DemoSlot.objects.filter(hackathon=hackathon).select_related('project__team')
    active = slots.filter(state='live').first()
    upcoming = (
        slots.filter(state__in=['waiting', 'ready', 'deferred'], project__state='approved')
        .order_by('position')[:3]
    )
    return {
        'server_time': timezone.now().isoformat(),
        'clock': services.clock_state(hackathon, active),
        'current': {
            'id': active.pk,
            'project': active.project.title,
            'team': active.project.team.name,
            'code': active.project.team.code,
            'position': active.position,
        }
        if active
        else None,
        'upcoming': [
            {
                'id': slot.pk,
                'project': slot.project.title,
                'team': slot.project.team.name,
                'code': slot.project.team.code,
                'position': slot.position,
                'state': slot.get_state_display(),
            }
            for slot in upcoming
        ],
    }


@event_view()
@require_GET
def stage(request):
    return page(
        request,
        'stage',
        data=stage_data(request.hackathon),
        slots=DemoSlot.objects.filter(hackathon=request.hackathon).select_related('project__team'),
        display=request.GET.get('display') == '1',
    )


@event_view()
@require_GET
def stage_state(request):
    return JsonResponse(stage_data(request.hackathon))


@event_view('organizer')
def manage_stage(request):
    h = request.hackathon
    token = None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'generate':
                services.generate_queue(h, request.user)
            elif action == 'token':
                token = secrets.token_urlsafe(32)
                with transaction.atomic():
                    StationToken.objects.filter(hackathon=h).delete()
                    StationToken.objects.create(
                        hackathon=h,
                        digest=hashlib.sha256(token.encode()).hexdigest(),
                        expires_at=timezone.now() + timedelta(hours=12),
                        created_by=request.user,
                    )
                    services.audit(h, request.user, '签发播放助手令牌')
            elif action == 'revoke':
                StationToken.objects.filter(hackathon=h).delete()
                services.audit(h, request.user, '撤销播放助手令牌')
            else:
                slot = get_object_or_404(DemoSlot, hackathon=h, pk=request.POST.get('slot'))
                services.stage_action(h, request.user, slot, action, request.POST.get('note', ''))
        except ValidationError as error:
            report_validation(request, error)
        else:
            messages.success(request, '路演状态已更新。')
        if not token:
            return redirect(url(h, 'manage-stage'))
    return page(
        request,
        'manage_stage',
        slots=DemoSlot.objects.filter(hackathon=h).select_related('project__team'),
        token=token,
        station_url=request.build_absolute_uri(url(h, 'station-state')),
        data=stage_data(h),
    )


def station_auth(request, hackathon_slug):
    hackathon = get_object_or_404(Hackathon.objects.select_related('event__organizer'), slug=hackathon_slug)
    if hackathon.program != Hackathon.Program.MAKER_CAMP:
        raise Http404
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        raise PermissionDenied
    digest = hashlib.sha256(header.removeprefix('Bearer ').encode()).hexdigest()
    with scope(event=hackathon.event):
        station = (
            StationToken.objects.filter(hackathon=hackathon, digest=digest, expires_at__gt=timezone.now())
            .select_related('created_by')
            .first()
        )
        if not station or not is_organizer(station.created_by, hackathon):
            raise PermissionDenied
    return hackathon


@require_GET
def station_state(request, hackathon_slug):
    h = station_auth(request, hackathon_slug)
    with scope(event=h.event):
        data = stage_data(h)
        queued = (
            DemoSlot.objects.filter(hackathon=h, state__in=['live', 'ready', 'waiting', 'deferred'])
            .filter(Q(state='live') | Q(project__state='approved'))
            .values('project_id')
        )
        data['assets'] = [
            {
                'id': str(asset.pk),
                'project_id': str(asset.project_id),
                'filename': asset.filename,
                'kind': asset.kind,
                'sha256': asset.checksum,
                'size': asset.size,
                'url': url(h, 'station-asset', asset_id=asset.pk),
            }
            for asset in Asset.objects.filter(project_id__in=queued)
        ]
        if data['current']:
            data['current']['project_id'] = str(DemoSlot.objects.get(pk=data['current']['id']).project_id)
        response = JsonResponse(data)
        response['Cache-Control'] = 'no-store'
        return response


@require_GET
def station_asset(request, hackathon_slug, asset_id):
    h = station_auth(request, hackathon_slug)
    with scope(event=h.event):
        asset = get_object_or_404(Asset, hackathon=h, pk=asset_id)
        response = asset_response(request, asset)
        response['Cache-Control'] = 'no-store'
        return response


def account_label(user):
    return user.fullname or f'账户 #{user.pk}'


def student_id_of(user):
    account = getattr(user, 'student_account', None)
    return account.student_id if account else ''


def account_reference(user):
    """交接表里用来区分同名的稳定依据：先学号，没有学号就用账户 ID，邮箱只作联系方式。"""
    return student_id_of(user) or f'#{user.pk}'


@event_view('organizer')
@require_GET
def export(request, kind):
    h = request.hackathon
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{h.slug}-{kind}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    if kind == 'participants':
        writer.writerow(
            [
                'participant_id',
                '姓名',
                '邮箱',
                '组织',
                '联系方式',
                'team_id',
                '队伍',
                '特殊安排',
                '签到',
                '证书姓名',
                '学号',
                '关键词',
                '一句话介绍',
            ]
        )
        for item in (
            Participant.objects.filter(hackathon=h)
            .select_related('user', 'team')
            .annotate(student_id=F('user__student_account__student_id'))
        ):
            writer.writerow(
                [
                    item.code,
                    item.name,
                    item.user.email,
                    item.school,
                    item.contact,
                    item.team.code if item.team else '',
                    item.team.name if item.team else '',
                    absence_summary(h, item.unavailable_slots, item.availability),
                    item.checked_in,
                    item.certificate_name,
                    item.student_id or '',
                    '、'.join(item.keywords),
                    item.tagline,
                ]
            )
    elif kind == 'results':
        writer.writerow(
            [
                'project_id',
                'team_id',
                '作品',
                '队伍',
                '最终得分',
                '采用评分数',
                '去极值份数',
                '算分规则',
                '有效评分',
                '应评数',
                '最佳突破提名',
                '最佳创意提名',
                '奖项',
            ]
        )
        for row in services.results(h):
            p = row['project']
            writer.writerow(
                [
                    p.code,
                    p.team.code,
                    p.title,
                    p.team.name,
                    row['average'],
                    row['counted'],
                    row['trimmed'],
                    rule_description(h),
                    row['count'],
                    row['expected'],
                    row['breakthrough'],
                    row['creativity'],
                    p.award,
                ]
            )
    elif kind == 'volunteers':
        writer.writerow(
            [
                '任务',
                '负责人',
                '负责人编号',
                '负责人邮箱',
                '备份',
                '备份编号',
                '备份邮箱',
                '状态',
                '开始',
                '完成',
                '核准分钟',
            ]
        )
        for item in VolunteerTask.objects.filter(hackathon=h).select_related(
            'owner__student_account', 'backup__student_account'
        ):
            writer.writerow(
                [
                    item.title,
                    account_label(item.owner),
                    account_reference(item.owner),
                    item.owner.email,
                    account_label(item.backup) if item.backup else '',
                    account_reference(item.backup) if item.backup else '',
                    item.backup.email if item.backup else '',
                    item.get_state_display(),
                    item.started_at,
                    item.finished_at,
                    item.approved_minutes,
                ]
            )
    elif kind == 'tickets':
        writer.writerow(
            [
                '工单号',
                '姓名',
                '本届编号',
                '账户编号',
                '队伍',
                '作品',
                '学号',
                '邮箱',
                '类别',
                '状态',
                '处理人',
                '开单时间',
                '最近沟通',
                '沟通条数',
                '标题',
            ]
        )
        tickets = list(
            SupportTicket.objects.filter(hackathon=h)
            .select_related('user__student_account', 'handler')
            .prefetch_related('messages')
        )
        people = {
            person.user_id: person
            for person in Participant.objects.filter(hackathon=h, user__in=[t.user for t in tickets]).select_related(
                'team'
            )
        }
        team_ids = [person.team_id for person in people.values() if person.team_id]
        team_projects = {}
        for project in Project.objects.filter(hackathon=h, team_id__in=team_ids):
            team_projects[project.team_id] = project
        for ticket in tickets:
            person = people.get(ticket.user_id)
            project = team_projects.get(person.team_id) if person and person.team_id else None
            messages_on_ticket = list(ticket.messages.all())
            last_message = messages_on_ticket[-1] if messages_on_ticket else None
            writer.writerow(
                [
                    ticket.pk,
                    person.name if person else account_label(ticket.user),
                    person.code if person else '',
                    account_reference(ticket.user),
                    (person.team.name if person.team_id else '尚未组队') if person else '未报名',
                    project.code if project else '',
                    student_id_of(ticket.user),
                    ticket.user.email or '',
                    ticket.get_category_display(),
                    ticket.get_state_display(),
                    account_label(ticket.handler) if ticket.handler else '',
                    ticket.created,
                    last_message.created if last_message else ticket.created,
                    len(messages_on_ticket),
                    ticket.subject,
                ]
            )
    elif kind == 'projects':
        writer.writerow(
            [
                'project_id',
                'team_id',
                '作品',
                '队伍',
                '赛道',
                '状态',
                '状态原因',
                'Demo',
                '仓库',
                '备用',
                '首次提交时间',
                '检查意见',
            ]
        )
        for p in Project.objects.filter(hackathon=h).select_related('team__track'):
            writer.writerow(
                [
                    p.code,
                    p.team.code,
                    p.title,
                    p.team.name,
                    p.team.track.name,
                    p.get_state_display(),
                    p.get_state_reason_display(),
                    p.demo_url,
                    p.repository_url,
                    p.backup_url,
                    p.submitted_at,
                    p.review_note,
                ]
            )
    else:
        raise Http404
    services.audit(h, request.user, '导出数据', kind)
    return response


@event_view('organizer')
@require_GET
def bundle(request):
    h = request.hackathon
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        assets = Asset.objects.filter(hackathon=h, project__submitted_at__isnull=False).select_related('project__team')
        for asset in assets:
            name = f'{asset.project.team.code}/{asset.project.code}/{asset.kind}{Path(asset.filename).suffix.lower()}'
            with asset.file.open('rb') as source, archive.open(name, 'w') as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
        archive.writestr('README.txt', '材料按 team_id / project_id 归档。请提前用现场设备检查；不要执行未知项目脚本。')
    size = output.tell()
    output.seek(0)
    services.audit(h, request.user, '下载路演材料包')
    return stream_file(request, output, size, f'{h.slug}-materials.zip')
