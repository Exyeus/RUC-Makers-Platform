import hashlib
import secrets
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from defusedcsv import csv
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_safe
from django_scopes import scope

from eventyay.hackathon import forms, services
from eventyay.hackathon.archive import zip_preview
from eventyay.hackathon.constants import KEYWORDS
from eventyay.hackathon.downloads import asset_response, stream_file
from eventyay.hackathon.models import (
    Announcement,
    Asset,
    AuditEntry,
    DemoSlot,
    Hackathon,
    JoinRequest,
    Participant,
    Project,
    Resource,
    ResourceRead,
    ScheduleItem,
    Score,
    StaffAssignment,
    StationToken,
    SupportTicket,
    Team,
    Track,
    UploadSession,
    VolunteerTask,
)
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.scoring import rule_description


def url(hackathon, name, **kwargs):
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': hackathon.slug, **kwargs})


def page(request, template, **context):
    hackathon = request.hackathon
    participant = request.participant
    navigation = [
        ('home', '总览', 'grid'),
        ('register', '个人资料', 'badge'),
        ('schedule', '活动日程', 'calendar'),
        ('announcements', '通知公告', 'bell'),
        ('resources', '学习与指南', 'book'),
        ('teams', '组队广场', 'users'),
        ('project', '我的作品', 'folder'),
        ('gallery', '作品展厅', 'spark'),
        ('stage', '路演现场', 'play'),
        ('support', '帮助与支持', 'help'),
        ('judge-login', '评委入口', 'chart'),
    ]
    staff_navigation = []
    if request.hackathon_organizer:
        staff_navigation = [
            ('manage', '活动总控', 'grid'),
            ('participants', '选手与队伍', 'users'),
            ('submissions', '作品检查', 'folder'),
            ('manage-stage', '路演调度', 'play'),
            ('results', '评审与成绩', 'chart'),
            ('scoring-rules', '算分规则', 'settings'),
            ('staff', '工作人员', 'badge'),
            ('content', '内容管理', 'book'),
            ('settings', '活动设置', 'settings'),
        ]
    if 'judge' in request.hackathon_roles:
        staff_navigation.extend([('judge-live', '现场打分', 'play'), ('judging', '全部评审', 'chart')])
    if request.hackathon_organizer or 'volunteer' in request.hackathon_roles:
        staff_navigation.append(('tasks', '志愿者工作台', 'badge'))
    common = {
        'hackathon': hackathon,
        'participant': participant,
        'is_organizer': request.hackathon_organizer,
        'is_staff_member': request.hackathon_organizer or bool(request.hackathon_roles),
        'is_judge': 'judge' in request.hackathon_roles,
        'now': timezone.now(),
        'navigation': [(name, label, icon, url(hackathon, name)) for name, label, icon in navigation],
        'staff_navigation': [(name, label, icon, url(hackathon, name)) for name, label, icon in staff_navigation],
        'active': request.resolver_match.url_name,
        'latest_notice': Announcement.objects.filter(hackathon=hackathon, audience='all').first(),
    }
    common.update(context)
    return render(request, f'hackathon/{template}.html', common)


def report_validation(request, error):
    for message in error.messages:
        messages.error(request, message)


@require_GET
def index(request):
    editions = list(Hackathon.objects.filter(is_public=True).select_related('event'))
    if len(editions) == 1:
        return redirect(url(editions[0], 'home'))
    return render(request, 'hackathon/index.html', {'editions': editions})


@event_view()
@require_GET
def home(request):
    h = request.hackathon
    participant = request.participant
    team = participant.team if participant else None
    project = Project.objects.filter(team=team).first() if team else None
    required = Resource.objects.filter(hackathon=h, required=True)
    read_count = (
        ResourceRead.objects.filter(resource__in=required, user=request.user).count()
        if request.user.is_authenticated
        else 0
    )
    checklist = [
        {
            'title': '完善参赛信息',
            'detail': '留下你的技能、联系方式与特殊时段',
            'done': bool(participant),
            'url': url(h, 'register'),
        },
        {
            'title': '找到你的队友',
            'detail': f'每队最多 {h.team_size} 人，也可以独立参赛',
            'done': bool(team),
            'url': url(h, 'teams'),
        },
        {
            'title': '读完参赛必读',
            'detail': f'已完成 {read_count} / {required.count()} 篇',
            'done': required.exists() and read_count == required.count(),
            'url': url(h, 'resources'),
        },
        {
            'title': '提交你的作品',
            'detail': 'Demo、仓库、电子海报与备用录屏',
            'done': bool(project and project.submitted_at),
            'url': url(h, 'project'),
        },
    ]
    return page(
        request,
        'home',
        team=team,
        project=project,
        journey_url=url(h, 'project' if participant else 'register'),
        checklist=checklist,
        progress=sum(item['done'] for item in checklist) * 25,
        tracks=Track.objects.filter(hackathon=h),
        participant_count=Participant.objects.filter(hackathon=h).count(),
        team_count=Team.objects.filter(hackathon=h).count(),
        project_count=Project.objects.filter(hackathon=h, submitted_at__isnull=False).count(),
        notices=Announcement.objects.filter(hackathon=h, audience='all')[:3],
        upcoming=ScheduleItem.objects.filter(hackathon=h, ends_at__gte=timezone.now())[:3],
        featured_resources=Resource.objects.filter(hackathon=h)[:3],
    )


@event_view('login')
def register(request):
    form = forms.ParticipantForm(request.POST or None, instance=request.participant)
    if request.method == 'POST' and form.is_valid():
        try:
            services.save_participant(request.hackathon, request.user, form)
        except ValidationError as error:
            form.add_error(None, error)
        else:
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
    keyword = request.GET.get('keyword', '')
    if keyword in KEYWORDS:
        seekers = seekers.filter(keywords__contains=[keyword])
    if query:
        seekers = seekers.filter(
            Q(name__icontains=query)
            | Q(tagline__icontains=query)
            | Q(skills__icontains=query)
            | Q(bio__icontains=query)
            | Q(keywords__icontains=query)
        )
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
        tracks=Track.objects.filter(hackathon=h),
        seekers=seekers.order_by('-updated')[:60],
        keywords=KEYWORDS,
        selected_keyword=keyword,
    )


@event_view('login')
def team_edit(request):
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
                team.save(update_fields=['captain', 'updated'])
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
        return page(request, 'project', project=None, needs_team=True)
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
            return JsonResponse(
                {'error': ' '.join(str(message) for errors in form.errors.values() for message in errors)}, status=400
            )
    return page(
        request,
        'project',
        project=own,
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
    blocked = project.state == 'queued' and any(
        task.expires_at <= timezone.now() or project.hackathon.freeze_at <= timezone.now() for task in tasks
    )
    data = {
        'state': project.state,
        'label': '排队提交已过期，请联系技术人员' if blocked else project.get_state_display(),
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
        item.save(update_fields=['state', 'updated'])
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
    items = Resource.objects.filter(hackathon=request.hackathon)
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
    item = get_object_or_404(Resource, hackathon=request.hackathon, pk=resource_id)
    read = request.user.is_authenticated and ResourceRead.objects.filter(resource=item, user=request.user).exists()
    if request.method == 'POST':
        if not request.user.is_authenticated:
            raise PermissionDenied
        ResourceRead.objects.get_or_create(hackathon=request.hackathon, resource=item, user=request.user)
        messages.success(request, '已记录阅读进度。')
        return redirect(url(request.hackathon, 'resource', resource_id=item.pk))
    return page(request, 'resource', resource=item, read=read)


@event_view()
@require_GET
def schedule(request):
    return page(request, 'schedule', items=ScheduleItem.objects.filter(hackathon=request.hackathon))


@event_view()
@require_GET
def announcements(request):
    items = Announcement.objects.filter(hackathon=request.hackathon)
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
        editions=Hackathon.objects.filter(is_public=True, gallery_open=True).exclude(pk=h.pk),
    )


@event_view('organizer')
@require_GET
def manage(request):
    h = request.hackathon
    return page(
        request,
        'manage',
        participant_count=Participant.objects.filter(hackathon=h).count(),
        checked_in=Participant.objects.filter(hackathon=h, checked_in=True).count(),
        team_count=Team.objects.filter(hackathon=h).count(),
        submitted=Project.objects.filter(hackathon=h, submitted_at__isnull=False).count(),
        pending=Project.objects.filter(hackathon=h, state='submitted').count(),
        missing=Team.objects.filter(hackathon=h).filter(Q(project__isnull=True) | Q(project__state='draft')),
        special=Participant.objects.filter(hackathon=h).exclude(availability='').select_related('team'),
        tickets=SupportTicket.objects.filter(hackathon=h).exclude(state='resolved').count(),
        log=AuditEntry.objects.filter(hackathon=h).select_related('actor')[:12],
    )


@event_view('organizer')
def participants(request):
    h = request.hackathon
    if request.method == 'POST':
        person = get_object_or_404(Participant, hackathon=h, pk=request.POST.get('participant'))
        person.checked_in = request.POST.get('checked_in') == 'yes'
        person.save(update_fields=['checked_in', 'updated'])
        services.audit(h, request.user, '更新签到', person.code)
        messages.success(request, '签到状态已更新。')
        return redirect(url(h, 'participants'))
    items = (
        Participant.objects.filter(hackathon=h)
        .select_related('team', 'user')
        .annotate(student_id=F('user__student_account__student_id'))
    )
    query = request.GET.get('q', '').strip()
    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(school__icontains=query)
            | Q(team__name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__student_account__student_id__icontains=query)
        )
    return page(request, 'participants', participants=items, query=query)


@event_view('organizer')
@require_GET
def submissions(request):
    items = Project.objects.filter(hackathon=request.hackathon).select_related('team__track').prefetch_related('assets')
    state = request.GET.get('state', '')
    if state:
        items = items.filter(state=state)
    return page(request, 'submissions', projects=items, states=Project.State.choices, state=state)


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
                current_project.save(update_fields=[*form.Meta.fields, 'updated'])
                if current_project.state != 'approved' or not current_project.equipment_checked:
                    DemoSlot.objects.filter(project=current_project, state='ready').update(state='waiting')
                services.audit(h, request.user, '作品检查', item.code, item.review_note)
                messages.success(request, '检查意见已保存，队伍可以在“我的作品”中查看。')
                return redirect(url(h, 'submissions'))
    return page(request, 'review', form=form, project=item, assets=item.assets.all())


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
    return page(request, 'intake', form=form, asset_form=asset_form, team=team, project=item)


@event_view('organizer')
def settings(request):
    h = request.hackathon
    form = forms.SettingsForm(request.POST or None, instance=h)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            current = services.lock_event(h)
            if form.cleaned_data['results_published'] and not current.results_published:
                rows = services.results(h)
                if not rows or any(not row['complete'] for row in rows):
                    form.add_error('results_published', '还有作品未完成全部有效评委评分，暂不能公布成绩。')
            if not form.errors:
                form.save(commit=False).save(update_fields=form.changed_data)
                services.audit(h, request.user, '更新活动设置', detail=', '.join(form.changed_data))
                messages.success(request, '活动设置已保存。')
                return redirect(url(h, 'settings'))
    return page(
        request,
        'form',
        form=form,
        title='活动设置',
        subtitle=f'所有时间以活动时区 {h.event.timezone} 为准。修改截止时间会记录在操作日志中。',
        button='保存设置',
    )


CONTENT = {
    'announcement': (Announcement, forms.AnnouncementForm, '通知'),
    'resource': (Resource, forms.ResourceForm, '教程与指南'),
    'schedule': (ScheduleItem, forms.ScheduleForm, '日程'),
    'track': (Track, forms.TrackForm, '赛道'),
}


@event_view('organizer')
@require_GET
def content(request):
    groups = [
        {'kind': kind, 'label': label, 'items': model.objects.filter(hackathon=request.hackathon)}
        for kind, (model, form, label) in CONTENT.items()
    ]
    return page(request, 'content', groups=groups)


@event_view('organizer')
def content_edit(request, kind, item_id=None):
    if kind not in CONTENT:
        raise Http404
    model, form_class, label = CONTENT[kind]
    instance = get_object_or_404(model, hackathon=request.hackathon, pk=item_id) if item_id else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.hackathon = request.hackathon
        item.save()
        services.audit(request.hackathon, request.user, f'更新{label}', item.pk)
        messages.success(request, f'{label}已保存。')
        return redirect(url(request.hackathon, 'content'))
    return page(request, 'form', form=form, title=f'编辑{label}' if instance else f'新增{label}', button='保存内容')


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


@event_view('login')
def support(request):
    h = request.hackathon
    form = forms.SupportForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.hackathon, item.user = h, request.user
        item.save()
        messages.success(request, f'工单 #{item.pk} 已收到，可以在本页跟踪处理结果。')
        return redirect(url(h, 'support'))
    items = SupportTicket.objects.filter(hackathon=h).select_related('user', 'handler')
    if not request.hackathon_organizer:
        items = items.filter(user=request.user)
    return page(request, 'support', form=form, tickets=items)


@event_view('organizer')
def support_reply(request, ticket_id):
    item = get_object_or_404(SupportTicket, hackathon=request.hackathon, pk=ticket_id)
    form = forms.ReplyForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.handler = request.user
        item.save()
        services.audit(request.hackathon, request.user, '处理支持工单', item.pk)
        return redirect(url(request.hackathon, 'support'))
    return page(
        request,
        'form',
        form=form,
        title=f'处理工单 #{item.pk} · {item.subject}',
        subtitle=item.body,
        button='回复并更新状态',
    )


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
    upcoming = slots.filter(state__in=['waiting', 'ready', 'deferred']).order_by('position')[:3]
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
        queued = DemoSlot.objects.filter(hackathon=h, state__in=['live', 'ready', 'waiting', 'deferred']).values(
            'project_id'
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
                    item.availability,
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
        writer.writerow(['任务', '负责人', '备份', '状态', '开始', '完成', '核准分钟'])
        for item in VolunteerTask.objects.filter(hackathon=h).select_related('owner', 'backup'):
            writer.writerow(
                [
                    item.title,
                    item.owner.email,
                    item.backup.email if item.backup else '',
                    item.get_state_display(),
                    item.started_at,
                    item.finished_at,
                    item.approved_minutes,
                ]
            )
    elif kind == 'projects':
        writer.writerow(
            ['project_id', 'team_id', '作品', '队伍', '赛道', '状态', 'Demo', '仓库', '备用', '提交时间', '检查意见']
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
