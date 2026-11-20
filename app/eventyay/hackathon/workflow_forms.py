from django import forms
from django.db.models import Q

from eventyay.base.models import User
from eventyay.hackathon.models import StaffAssignment, Track
from eventyay.hackathon.workflow_models import JudgingRound
from eventyay.hackathon.workflow_services import criterion_max


# 需求文档 6.1：各维度按满分直接打分，四项相加为总分（满分 100）。
ROUND_CRITERIA = {
    'preliminary': [
        ('fit', '赛道契合与问题判断', 25),
        ('function', '功能与完成度', 40),
        ('value', '应用价值与可验证性', 25),
        ('clarity', '视频表达', 10),
    ],
    'final': [
        ('fit', '问题与赛道契合', 25),
        ('demo', '现场演示及完成度', 35),
        ('value', '实际价值与验证', 25),
        ('answers', '答问与表达', 15),
    ],
}


class RoundForm(forms.Form):
    kind = forms.ChoiceField(label='评审阶段', choices=JudgingRound.Kind.choices)
    name = forms.CharField(label='轮次名称', max_length=100)
    panel = forms.CharField(
        label='评委分工',
        widget=forms.Textarea(attrs={'rows': 6}),
        help_text=(
            '每行：评委邮箱 | 赛道编号（多个逗号分隔；全赛道留空） | 非学生或学生。'
            '初评每赛道2名独立打分评委，另设非学生复核人；决赛同组至少6名非学生，覆盖所有入围队伍。'
        ),
    )
    reviewers = forms.CharField(
        label='初评非学生复核人',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=(
            '每行：复核人邮箱 | 赛道编号（多个逗号分隔；全赛道留空）。'
            '复核人与对应赛道两名打分评委分开；可由总负责人兼任。'
        ),
    )
    reviewer_nonstudent = forms.BooleanField(
        label='已核实初评复核人为非学生，且不兼任所复核赛道打分评委', required=False
    )
    signer = forms.ModelChoiceField(label='非学生总负责人（签认人）', queryset=User.objects.none())
    signer_nonstudent = forms.BooleanField(label='已核实签认人为本届非学生总负责人，学生不能代签')
    aggregation = forms.ChoiceField(
        label='算分方法', choices=[('mean', '全部有效评分取平均'), ('trimmed', '去极值后平均')], initial='mean'
    )
    trim = forms.IntegerField(label='各去掉最高/最低份数', min_value=0, max_value=10, initial=1)
    acknowledge = forms.BooleanField(label='确认冻结本轮规则、评委资格、赛道分工、材料版本与回避集合；后续更正另建版本')

    def __init__(self, *args, hackathon, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.hackathon = hackathon
        ids = StaffAssignment.objects.filter(hackathon=hackathon, role='judge').values_list('user_id', flat=True)
        self.fields['signer'].queryset = User.objects.filter(
            Q(pk__in=ids) | Q(pk=actor.pk), is_active=True, is_spam=False
        )
        self.actors = {
            user.email.lower(): user
            for user in User.objects.filter(Q(pk__in=ids) | Q(pk=actor.pk), is_active=True, is_spam=False)
        }
        self.tracks = {str(track.pk) for track in Track.objects.filter(hackathon=hackathon)}

    def clean_panel(self):
        rows = []
        for index, line in enumerate(self.cleaned_data['panel'].splitlines(), 1):
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split('|')]
            if len(parts) != 3 or parts[0].lower() not in self.actors or parts[2] not in {'非学生', '学生'}:
                raise forms.ValidationError(f'第{index}行请填写本届可用评委邮箱、赛道编号和明确的非学生/学生身份。')
            tracks = [value.strip() for value in parts[1].split(',') if value.strip()]
            if set(tracks) - self.tracks:
                raise forms.ValidationError(f'第{index}行含本活动以外的赛道编号。')
            rows.append(
                {'user_id': str(self.actors[parts[0].lower()].pk), 'tracks': tracks, 'nonstudent': parts[2] == '非学生'}
            )
        return rows

    def clean_reviewers(self):
        rows = []
        for line in self.cleaned_data.get('reviewers', '').splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split('|')]
            if len(parts) != 2 or parts[0].lower() not in self.actors:
                raise forms.ValidationError('请按复核人邮箱 | 赛道编号填写，复核人须为本届可用评委或当前主办。')
            tracks = [value.strip() for value in parts[1].split(',') if value.strip()]
            if set(tracks) - self.tracks:
                raise forms.ValidationError('复核赛道不属于当前活动。')
            rows.append(
                {
                    'user_id': str(self.actors[parts[0].lower()].pk),
                    'name': self.actors[parts[0].lower()].get_display_name(),
                    'tracks': tracks,
                    'nonstudent': True,
                }
            )
        return rows

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('kind') == 'preliminary' and (
            not cleaned.get('reviewers') or not cleaned.get('reviewer_nonstudent')
        ):
            self.add_error('reviewers', '初评请登记并核实独立的非学生复核人。')
        return cleaned

    def rules(self):
        kind = self.cleaned_data['kind']
        return {
            'criteria': [{'key': key, 'label': label, 'weight': weight} for key, label, weight in ROUND_CRITERIA[kind]],
            'scale': 'points',
            'aggregation': self.cleaned_data['aggregation'],
            'trim': self.cleaned_data['trim'],
            'minimum_judges': 2 if kind == 'preliminary' else 6,
            'minimum_nonstudent': 0 if kind == 'preliminary' else 6,
            'reviewers': self.cleaned_data.get('reviewers', []),
        }


class RoundScoreForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0, initial=0)
    feedback = forms.CharField(
        label='改进建议', required=False, max_length=3000, widget=forms.Textarea(attrs={'rows': 3})
    )

    def __init__(self, *args, round, existing=None, confirm=False, **kwargs):
        initial = kwargs.setdefault('initial', {})
        if existing:
            initial.update(existing.values, revision=existing.revision, feedback=existing.feedback)
        super().__init__(*args, **kwargs)
        self.round = round
        for criterion in reversed(round.rules['criteria']):
            top = criterion_max(round.rules, criterion)
            self.fields[criterion['key']] = forms.IntegerField(
                label=f'{criterion["label"]}（满分 {top}）'
                if round.rules.get('scale') == 'points'
                else f'{criterion["label"]} · {criterion["weight"]}%',
                min_value=0,
                max_value=top,
                required=confirm,
            )
            self.fields.move_to_end(criterion['key'], last=False) if hasattr(self.fields, 'move_to_end') else None
        self.order_fields([row['key'] for row in round.rules['criteria']] + ['feedback', 'revision'])

    def values(self):
        return {row['key']: self.cleaned_data[row['key']] for row in self.round.rules['criteria']}


class SnapshotForm(forms.Form):
    reason = forms.CharField(label='名单依据 / 更正原因', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))
    seats = forms.IntegerField(label='每赛道入围名额', min_value=1, max_value=100, initial=8)
    alternates = forms.IntegerField(
        label='每赛道候补人数',
        min_value=0,
        max_value=100,
        help_text='方案未固定候补人数，请主办方在复核时明确；可将剩余队伍列为候补。',
    )
    selections = forms.CharField(
        label='同分人工复核顺序（有边界同分时填写）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='每行：赛道编号 | 本赛道全部作品UUID，按复核后的顺序用逗号分隔；依据写在上方。',
    )
    awards = forms.CharField(
        label='获奖分配（仅决赛）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='每行：作品UUID | 奖项名称；未列出的队伍不授奖。',
    )

    def clean_selections(self):
        values = {}
        for line in self.cleaned_data['selections'].splitlines():
            if not line.strip():
                continue
            parts = [value.strip() for value in line.split('|')]
            if len(parts) != 2 or parts[0] in values:
                raise forms.ValidationError('请按赛道编号 | 作品UUID,作品UUID填写，每条赛道一行。')
            values[parts[0]] = [value.strip() for value in parts[1].split(',') if value.strip()]
        return values

    def clean_awards(self):
        values = {}
        for line in self.cleaned_data['awards'].splitlines():
            if not line.strip():
                continue
            parts = [value.strip() for value in line.split('|')]
            if len(parts) != 2 or not parts[1] or len(parts[1]) > 200 or parts[0] in values:
                raise forms.ValidationError('每行请填写一个作品UUID | 奖项名称，不能重复。')
            values[parts[0]] = parts[1]
        return values


class ContactForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    state = forms.ChoiceField(
        label='联络状态',
        choices=[
            ('pending', '待联络'),
            ('sent', '已送达'),
            ('accepted', '已确认（人工回填）'),
            ('declined', '已拒绝'),
            ('expired', '已超时'),
        ],
    )
    channel = forms.CharField(label='送达渠道（微信 / 电话 / 邮件等）', max_length=100, required=False)
    contact_text = forms.CharField(label='联系人', max_length=200, required=False)
    sent_at = forms.DateTimeField(
        label='实际送达时间',
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
    )
    deadline = forms.DateTimeField(
        label='确认截止时间',
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        help_text='首批送达后15分钟，候补递补送达后5分钟；均由服务器记录。',
    )
    arrived = forms.BooleanField(label='已到场（首批13:05前）', required=False)
    equipment_ready = forms.BooleanField(label='设备已核对', required=False)
    note = forms.CharField(
        label='联络记录 / 异常说明', max_length=2000, required=False, widget=forms.Textarea(attrs={'rows': 3})
    )
