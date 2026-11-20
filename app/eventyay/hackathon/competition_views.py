"""Organizer pages for competition day: the plan, the command center and the roadshow order."""

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from eventyay.base.models import User
from eventyay.hackathon import competition_day, roadshow_package, services
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.downloads import stream_file
from eventyay.hackathon.models import StaffAssignment, Track
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url
from eventyay.hackathon.workflow_models import CompetitionPlan


class PlanForm(forms.Form):
    signer = forms.ModelChoiceField(label='评审总负责人（签认名单）', queryset=User.objects.none(), required=False)
    seats = forms.IntegerField(label='每赛道入围', min_value=1, max_value=50, initial=8)
    alternates = forms.IntegerField(label='每赛道候补', min_value=0, max_value=20, initial=2)
    roadshow_start = forms.DateTimeField(
        label='路演开始', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
    )
    slot_minutes = forms.IntegerField(label='每队时长（分钟，含换场）', min_value=1, max_value=60, initial=5)
    confirm_minutes = forms.IntegerField(label='入围确认时限（分钟）', min_value=1, max_value=240, initial=15)
    auto_lock = forms.BooleanField(label='截止后自动锁定并开放初评', required=False, initial=True)

    def __init__(self, *args, hackathon, actor, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracks = list(Track.objects.filter(hackathon=hackathon, state='published').order_by('pk'))
        judge_ids = StaffAssignment.objects.filter(hackathon=hackathon, role='judge').values_list('user_id', flat=True)
        judges = User.objects.filter(pk__in=judge_ids, is_active=True, is_spam=False).order_by('fullname')
        choices = [(str(user.pk), user.get_display_name()) for user in judges]
        self.fields['signer'].queryset = User.objects.filter(
            Q(pk__in=judge_ids) | Q(pk=actor.pk), is_active=True, is_spam=False
        )
        self.fields['signer'].label_from_instance = lambda user: user.get_display_name()
        for track in self.tracks:
            self.fields[f'pre_{track.pk}'] = forms.MultipleChoiceField(
                label=track.name, choices=choices, required=False, widget=forms.CheckboxSelectMultiple
            )
            self.fields[f'rev_{track.pk}'] = forms.ChoiceField(
                label=f'{track.name} 复核人', choices=[('', '—')] + choices, required=False
            )
        self.fields['final'] = forms.MultipleChoiceField(
            label='决赛评委', choices=choices, required=False, widget=forms.CheckboxSelectMultiple
        )
        if plan and not self.is_bound:
            self.initial.update(
                signer=plan.signer_id,
                seats=plan.seats,
                alternates=plan.alternates,
                roadshow_start=timezone.localtime(plan.roadshow_start).strftime('%Y-%m-%dT%H:%M')
                if plan.roadshow_start
                else None,
                slot_minutes=plan.slot_minutes,
                confirm_minutes=plan.confirm_minutes,
                auto_lock=plan.auto_lock,
                final=[str(pk) for pk in plan.final_panel],
            )
            for track in self.tracks:
                self.initial[f'pre_{track.pk}'] = [str(pk) for pk in plan.preliminary_panel.get(str(track.pk), [])]
                self.initial[f'rev_{track.pk}'] = plan.reviewers.get(str(track.pk), '')

    def flow_fields(self):
        names = ('signer', 'seats', 'alternates', 'roadshow_start', 'slot_minutes', 'confirm_minutes', 'auto_lock')
        return [self[name] for name in names]

    def track_rows(self):
        return [(track, self[f'pre_{track.pk}'], self[f'rev_{track.pk}']) for track in self.tracks]

    def save(self, hackathon, actor):
        data = self.cleaned_data
        plan = CompetitionPlan.objects.filter(hackathon=hackathon).first() or CompetitionPlan(hackathon=hackathon)
        if plan.locked_at and plan.preliminary_panel != {
            str(track.pk): data[f'pre_{track.pk}'] for track in self.tracks
        }:
            raise ValidationError('初评已开始，初评评委分工不能再修改。')
        plan.preliminary_panel = {str(track.pk): data[f'pre_{track.pk}'] for track in self.tracks}
        plan.reviewers = {str(track.pk): data[f'rev_{track.pk}'] for track in self.tracks if data[f'rev_{track.pk}']}
        plan.final_panel = data['final']
        plan.signer = data['signer']
        for name in ('seats', 'alternates', 'roadshow_start', 'slot_minutes', 'confirm_minutes', 'auto_lock'):
            setattr(plan, name, data[name])
        plan.updated_by = actor
        plan.save()
        services.audit(hackathon, actor, '保存比赛日配置', plan.pk)
        return plan


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def plan(request):
    h = request.hackathon
    current = competition_day.plan_for(h)
    form = PlanForm(request.POST or None, hackathon=h, actor=request.user, plan=current)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save(h, request.user)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, '比赛日配置已保存。')
            return redirect(url(h, 'competition-plan'))
    return page(
        request,
        'competition_plan',
        page_title='比赛日配置',
        form=form,
        plan=current,
        problems=competition_day.plan_problems(h, current) if current else [],
    )


STAGES = [
    ('deadline', '提交截止'),
    ('preliminary', '初评'),
    ('review', '入围复核'),
    ('publish', '发布'),
    ('roadshow', '决赛路演'),
    ('awards', '获奖确认'),
]
STAGE_INDEX = {
    'deadline': 0,
    'preliminary': 1,
    'review': 2,
    'publish': 3,
    'roadshow': 4,
    'final': 4,
    'awards': 5,
    'done': 6,
}


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def command(request):
    h = request.hackathon
    if request.method == 'POST':
        action = request.POST.get('action', '')
        state = competition_day.status(h)
        try:
            if action == 'lock':
                result = competition_day.lock_and_start(h)
                if result != 'done':
                    raise ValidationError(competition_day.plan_for(h).last_error or '尚未到截止时间。')
                messages.success(request, '已锁定作品并开放初评。')
            elif action == 'close-preliminary' and state['preliminary']:
                workflow.set_round_open(h, request.user, state['preliminary']['round'], False)
                messages.success(request, '初评已关闭。')
            elif action == 'shuffle':
                order = competition_day.shuffle_order(h, request.user)
                messages.success(request, f'已生成路演顺序 v{order.version}。')
            elif action == 'publish' and state['order']:
                competition_day.publish_order(h, request.user, state['order'])
                messages.success(request, '名单与路演顺序已发布，入围队伍已收到站内通知。')
            elif action == 'start-final':
                competition_day.start_final(h, request.user)
                messages.success(request, '决赛评分已开放。')
            elif action == 'close-final' and state['final']:
                workflow.set_round_open(h, request.user, state['final']['round'], False)
                messages.success(request, '决赛评分已关闭。')
            else:
                raise ValidationError('当前阶段不能执行这个操作。')
        except ValidationError as error:
            messages.error(request, '；'.join(error.messages))
        return redirect(url(h, 'competition-day'))
    state = competition_day.status(h)
    current = STAGE_INDEX[state['stage']]
    stages = [
        {
            'key': key,
            'label': label,
            'no': f'0{index + 1}',
            'state': 'done' if index < current else 'now' if index == current else 'next',
        }
        for index, (key, label) in enumerate(STAGES)
    ]
    plan = state['plan']
    order_rows = []
    if state['order']:
        contacts = {str(contact.team_id): contact for contact in state['contacts']}
        for row in state['order'].entries:
            order_rows.append(
                {
                    **row,
                    'expected': competition_day.expected_start(plan, row['position']),
                    'contact': contacts.get(row['team_id']),
                }
            )
    return page(request, 'competition_day', page_title='比赛日', state=state, stages=stages, order_rows=order_rows)


@event_view('organizer')
@require_http_methods(['GET'])
def package(request):
    h = request.hackathon
    order = competition_day.current_order(h)
    if order is None:
        messages.error(request, '请先生成路演顺序。')
        return redirect(url(h, 'competition-day'))
    tracks = {str(track.pk): track.name for track in Track.objects.filter(hackathon=h)}
    try:
        source, size = roadshow_package.build(order, tracks)
    except ValidationError as error:
        messages.error(request, '路演材料包未生成：' + '；'.join(error.messages))
        return redirect(url(h, 'competition-day'))
    services.audit(h, request.user, '下载路演材料包', order.pk, f'顺序 v{order.version}')
    return stream_file(request, source, size, roadshow_package.package_name(order))
