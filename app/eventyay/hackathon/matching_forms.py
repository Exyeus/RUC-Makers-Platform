from datetime import UTC, datetime

from django import forms
from django.core.exceptions import ValidationError

from eventyay.hackathon.calendar import attendance_hours, available_half_hours
from eventyay.hackathon.constants import KEYWORD_CHOICES
from eventyay.hackathon.models import Idea, MatchingProfile, Track
from eventyay.hackathon.widgets import CategorizedTags, UnavailableHoursField


ROLES = (
    ('frontend', '前端开发'),
    ('backend', '后端开发'),
    ('ai', 'AI / 数据'),
    ('design', '交互与视觉设计'),
    ('research', '用户研究'),
    ('product', '产品与策划'),
    ('testing', '测试'),
    ('presentation', '表达与演示'),
)
ROLE_LABELS = dict(ROLES)
GOALS = [('explore', '探索与学习'), ('demo', '完成可用 Demo'), ('prize', '高投入冲奖')]


def tags(value: str) -> list[str]:
    result = sorted({tag.strip() for tag in value.replace('，', ',').split(',') if tag.strip()})
    if any(len(tag) > 40 for tag in result):
        raise ValidationError('每个标签不超过 40 字；数量不限。')
    return result


class ProfileForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    independent_roles = forms.MultipleChoiceField(
        label='能独立完成的角色', choices=ROLES, required=False, widget=forms.CheckboxSelectMultiple
    )
    supported_roles = forms.MultipleChoiceField(
        label='在指导下能完成的角色', choices=ROLES, required=False, widget=forms.CheckboxSelectMultiple
    )
    learning_roles = forms.MultipleChoiceField(
        label='愿意学习（不计为已有能力）', choices=ROLES, required=False, widget=forms.CheckboxSelectMultiple
    )
    needs = forms.MultipleChoiceField(
        label='希望伙伴贡献的角色', choices=ROLES, required=False, widget=forms.CheckboxSelectMultiple
    )
    interests = forms.CharField(
        label='自定义兴趣 / 问题标签（数量不限）',
        required=False,
        help_text='用逗号分隔，例如：校园活动, 无障碍, 教育',
    )
    suggested_interests = forms.MultipleChoiceField(
        label='按类别添加兴趣标签', choices=KEYWORD_CHOICES, required=False, widget=CategorizedTags
    )
    availability_confirmed = forms.BooleanField(label='已确认这两天的安排，未圈选的小时可以协作', required=False)
    slots = forms.CharField(
        label='可共同协作的时段',
        max_length=12000,
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='每行一个半小时时段的开始时间，含时区，例如 2026-11-01T19:00+08:00。留空表示尚未确认。',
    )
    goal = forms.ChoiceField(label='参赛目标', choices=[('', '尚未确定'), *GOALS], required=False)
    async_ok = forms.BooleanField(label='明确接受异步协作', required=False)
    visibility = forms.ChoiceField(label='资料可见范围', choices=MatchingProfile.Visibility.choices)
    opt_in = forms.BooleanField(label='同意将以上资料用于本活动匹配', required=False)
    batch_opt_in = forms.BooleanField(label='主动参加本轮集中撮合（7 天内有效，结果仍需本人确认）', required=False)
    help_requested = forms.BooleanField(label='需要工作人员协助找队伍', required=False)
    contact = forms.CharField(label='交流联系方式（单独授权）', max_length=200, required=False)
    share_on_accept = forms.BooleanField(label='双方同意交流后向对方披露此联系方式', required=False)

    def __init__(self, *args, hackathon=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hackathon = hackathon
        if hackathon:
            self.fields['slots'].widget = forms.HiddenInput(attrs={'disabled': True})
            self.fields['unavailable_slots'] = UnavailableHoursField(
                hackathon=hackathon,
                label='无法到场的时段（两天，精确到小时）',
                help_text='圈选无法参与的小时；确认安排后，其余小时用于匹配。具体缺席时段不会公开。',
            )
            initial_slots = set(self.initial.get('slots', '').splitlines())
            if initial_slots or self.initial.get('availability_confirmed'):
                available = {int(datetime.fromisoformat(value).timestamp()) // 1800 for value in initial_slots}
                self.initial['unavailable_slots'] = [
                    hour
                    for _, hours in attendance_hours(hackathon)
                    for hour, _ in hours
                    if int(hour) * 2 not in available or int(hour) * 2 + 1 not in available
                ]
                self.initial['availability_confirmed'] = True
            self.order_fields(
                [
                    'version',
                    'independent_roles',
                    'supported_roles',
                    'learning_roles',
                    'needs',
                    'suggested_interests',
                    'interests',
                    'unavailable_slots',
                    'availability_confirmed',
                ]
            )

    def clean_interests(self):
        return tags(self.cleaned_data['interests'])

    def clean_slots(self):
        value = self.cleaned_data['slots'].strip()
        if not value:
            return None
        result = set()
        for line in value.splitlines():
            try:
                instant = datetime.fromisoformat(line.strip())
            except ValueError as error:
                raise ValidationError('请使用包含时区的 ISO 时间，例如 2026-11-01T19:00+08:00。') from error
            if instant.tzinfo is None or instant.minute not in {0, 30} or instant.second or instant.microsecond:
                raise ValidationError('时段须含时区，并从整点或半点开始。')
            if self.hackathon:
                event = self.hackathon.event
                if instant < event.date_from or (
                    event.date_to and instant.timestamp() + 1800 > event.date_to.timestamp()
                ):
                    raise ValidationError('协作时段须位于本活动开始与结束时间内。')
            result.add(int(instant.timestamp()) // 1800)
        if len(result) > 400:
            raise ValidationError('最多填写 400 个半小时时段。')
        return sorted(result)

    def clean(self):
        data = super().clean()
        data['interests'] = sorted(set(data.get('interests', []) + data.get('suggested_interests', [])))
        if self.hackathon and data.get('availability_confirmed') and 'unavailable_slots' in data:
            data['slots'] = available_half_hours(self.hackathon, data['unavailable_slots'])
        data['skills'] = {role: 0.5 for role in data.get('supported_roles', [])}
        data['skills'].update({role: 1.0 for role in data.get('independent_roles', [])})
        if data.get('batch_opt_in') and (not data.get('opt_in') or data.get('visibility') == 'paused'):
            self.add_error('batch_opt_in', '参加集中撮合需同意匹配且未暂停。')
        return data


def profile_initial(profile, contact) -> dict:
    if profile is None:
        return {'visibility': 'paused', 'version': 0}
    return {
        'version': profile.version,
        'independent_roles': [k for k, v in profile.skills.items() if v == 1],
        'supported_roles': [k for k, v in profile.skills.items() if v == 0.5],
        'learning_roles': profile.learning_roles,
        'needs': list(profile.needs),
        'interests': ', '.join(profile.interests),
        'slots': '\n'.join(
            datetime.fromtimestamp(s * 1800, UTC).isoformat(timespec='minutes') for s in profile.slots or []
        ),
        'availability_confirmed': profile.slots is not None,
        'goal': profile.goal,
        'async_ok': profile.async_ok,
        'visibility': profile.visibility,
        'opt_in': profile.opt_in,
        'batch_opt_in': profile.batch_opt_in,
        'help_requested': profile.help_requested,
        'contact': contact.text if contact else '',
        'share_on_accept': contact.share_on_accept if contact else False,
    }


class IdeaForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)
    interest_tags = forms.CharField(label='兴趣标签（逗号分隔，数量不限）')
    roles = forms.MultipleChoiceField(label='需要的角色', choices=ROLES, widget=forms.CheckboxSelectMultiple)

    def clean_interest_tags(self):
        return tags(self.cleaned_data['interest_tags'])

    class Meta:
        model = Idea
        fields = ['title', 'problem', 'audience', 'solution', 'mode', 'allow_async', 'state']


class ProposalForm(forms.Form):
    idea = forms.ModelChoiceField(label='共同选择的 Idea', queryset=Idea.objects.none())
    member_ids = forms.CharField(
        label='完整成员编号', max_length=1000, help_text='包含本人及现队所有成员，使用人员卡片上的完整编号，逗号分隔。'
    )
    team_name = forms.CharField(label='队伍名称', max_length=80)
    track = forms.ModelChoiceField(label='赛道', queryset=Track.objects.none())
    agreement = forms.CharField(
        label='合作约定',
        max_length=2000,
        widget=forms.Textarea,
        help_text='写明最小 Demo、各自角色、投入时间、联系与退出方式。',
    )

    def __init__(self, *args, hackathon, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['idea'].queryset = Idea.objects.filter(hackathon=hackathon, state='open')
        self.fields['track'].queryset = Track.objects.filter(hackathon=hackathon)
