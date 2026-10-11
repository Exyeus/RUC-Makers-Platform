from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from eventyay.base.models import User
from eventyay.hackathon import forms, services
from eventyay.hackathon.models import DemoSlot, Project, Score, StaffAssignment
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.scoring import CRITERIA, rule_description
from eventyay.hackathon.views import page, url


def current_presentation(request):
    slot = DemoSlot.objects.filter(hackathon=request.hackathon, state='live').select_related('project__team').first()
    if not slot:
        return None
    own_team = request.participant and request.participant.team_id == slot.project.team_id
    can_score = slot.project.state == 'approved' and not own_team
    return {
        'id': str(slot.project_id),
        'title': slot.project.title,
        'team': slot.project.team.name,
        'position': slot.position,
        'can_score': can_score,
        'score_url': url(request.hackathon, 'score', project_id=slot.project_id) + '?follow=1' if can_score else None,
    }


def judging_context(request):
    h = request.hackathon
    projects = (
        Project.objects.filter(hackathon=h, state='approved')
        .select_related('team__track', 'slot')
        .order_by('slot__position', 'created')
    )
    if request.participant and request.participant.team_id:
        projects = projects.exclude(team_id=request.participant.team_id)
    scores = {item.project_id: item for item in Score.objects.filter(hackathon=h, judge=request.user)}
    for item in scores.values():
        item.hackathon = h
    return {
        'entries': [{'project': item, 'score': scores.get(item.pk)} for item in projects],
        'current': current_presentation(request),
        'rule_description': rule_description(h),
        'confirmed': sum(item.final for item in scores.values()),
        'breakthrough': sum(item.final and item.breakthrough for item in scores.values()),
        'creativity': sum(item.final and item.creativity for item in scores.values()),
    }


@event_view('judge')
@require_GET
def judging(request):
    return page(request, 'judging', **judging_context(request))


@event_view('judge')
@require_GET
def live(request):
    current = current_presentation(request)
    if current and current['can_score']:
        return redirect(current['score_url'])
    return page(request, 'judge_waiting', follow_live=True, **judging_context(request))


@event_view('judge')
@require_GET
def state(request):
    h = request.hackathon
    return JsonResponse(
        {
            'current': current_presentation(request),
            'scoring_revision': h.scoring_revision,
            'open': h.judging_open and not h.results_published,
        }
    )


@event_view('judge')
@require_http_methods(['GET', 'POST'])
def score(request, project_id):
    h = request.hackathon
    item = get_object_or_404(
        Project.objects.select_related('team__track'), hackathon=h, pk=project_id, state='approved'
    )
    if request.participant and request.participant.team_id == item.team_id:
        raise PermissionDenied
    existing = Score.objects.filter(project=item, judge=request.user).first()
    form = forms.ScoreForm(request.POST if request.method == 'POST' else None, instance=existing, hackathon=h)
    follow_live = request.GET.get('follow') == '1'
    if request.method == 'POST' and form.is_valid():
        try:
            services.save_score(h, request.user, item, form)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                f'「{item.team.name}」评分已保存。'
                + ('已确认，计入统计。' if form.cleaned_data['final'] else '当前为草稿，尚不计入统计。'),
            )
            return redirect(url(h, 'judge-live') if follow_live else url(h, 'score', project_id=item.pk))
    context = judging_context(request)
    projects = [entry['project'] for entry in context['entries']]
    position = next(index for index, project in enumerate(projects) if project.pk == item.pk)
    return page(
        request,
        'score',
        form=form,
        project=item,
        assets=item.assets.all(),
        follow_live=follow_live,
        previous=projects[position - 1] if position else None,
        following=projects[position + 1] if position + 1 < len(projects) else None,
        **context,
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def rules(request):
    h = request.hackathon
    description = rule_description(h)
    locked = h.results_published or Score.objects.filter(hackathon=h).exists()
    data = request.POST if request.method == 'POST' else None
    if data is not None and request.POST.get('action') == 'defaults':
        data = {f'{name}_weight': default for name, label, default in CRITERIA}
        data.update(score_aggregation='mean', trim_extremes=1)
    form = forms.ScoringRulesForm(data, instance=h)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            current = services.lock_event(h)
            if current.results_published or Score.objects.filter(hackathon=current).exists():
                locked = True
                form.add_error(None, '已开始录入评分，算分规则已锁定，避免中途变更影响公平性。')
            else:
                for field in form.Meta.fields:
                    setattr(current, field, form.cleaned_data[field])
                current.scoring_revision += 1
                current.save(update_fields=[*form.Meta.fields, 'scoring_revision'])
                services.audit(current, request.user, '更新算分规则', detail=rule_description(current))
                messages.success(request, '算分规则已保存，所有评委和成绩汇总将使用同一规则。')
                return redirect(url(h, 'scoring-rules'))
    return page(request, 'scoring_rules', form=form, locked=locked, rule_description=description)


@sensitive_post_parameters('password', 'password_confirm')
@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def create_judge(request):
    form = forms.JudgeAccountForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                h = services.lock_event(request.hackathon)
                if h.results_published:
                    raise ValidationError('成绩已公布，评委名单已锁定。')
                user = User.objects.create_user(
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password'],
                    fullname=form.cleaned_data['name'],
                    locale='zh-hans',
                    timezone=h.event.timezone,
                )
                StaffAssignment.objects.create(hackathon=h, user=user, role='judge')
                services.audit(h, request.user, '创建评委账户', user.pk)
        except IntegrityError:
            form.add_error('email', '此邮箱已有账户，请直接为已有账户分配评委角色。')
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, '评委账户已创建。请将登录邮箱、密码和评委登录链接交给本人。')
            return redirect(url(h, 'staff'))
    return page(
        request,
        'form',
        form=form,
        title='创建评委账户',
        subtitle='创建后仅授予本届活动的评委权限。已有账户可直接在工作人员页面分配角色。',
        button='创建评委账户',
    )
