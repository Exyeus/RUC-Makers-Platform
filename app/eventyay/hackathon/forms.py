import ipaddress
import struct
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from eventyay.base.forms.auth import LoginForm
from eventyay.base.models import User
from eventyay.hackathon.constants import ALLOWED_EXTENSIONS, KEYWORD_CHOICES, KEYWORDS, MAX_UPLOAD_SIZE, POSTER_MAX_SIZE
from eventyay.hackathon.models import (
    Announcement,
    Asset,
    BonusRule,
    CommunityPost,
    CommunityResponse,
    Hackathon,
    Participant,
    Project,
    Resource,
    ScheduleItem,
    Score,
    StaffAssignment,
    StudentAccount,
    SupportTicket,
    Team,
    Track,
    VolunteerTask,
)
from eventyay.hackathon.scoring import CRITERIA, weights
from eventyay.hackathon.widgets import CategorizedTags, TagsField, UnavailableHoursField


class OrganizerLoginForm(LoginForm):
    error_messages = {
        'invalid_login': '邮箱或密码不正确，请重试。',
        'rate_limit': '尝试过于频繁，请 5 分钟后重试。',
        'inactive': '此账户已停用，请联系主办方。',
        'spam': '此账户已暂停使用，请联系主办方。',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].label = '账户邮箱'
        self.fields['email'].widget.attrs['autocomplete'] = 'username'
        self.fields['password'].label = '密码'
        self.fields['password'].widget.attrs['autocomplete'] = 'current-password'
        if 'keep_logged_in' in self.fields:
            self.fields['keep_logged_in'].label = '保持登录'


class StudentLoginForm(forms.Form):
    student_id = forms.RegexField(
        r'^[A-Za-z0-9]{4,32}$',
        label='学号',
        max_length=32,
        help_text='4–32 位数字或英文字母，不区分字母大小写。',
        widget=forms.TextInput(attrs={'autocomplete': 'username'}),
    )
    password = forms.CharField(
        label='密码',
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def clean_student_id(self):
        return self.cleaned_data['student_id'].upper()


class StudentSignupForm(StudentLoginForm):
    name = forms.CharField(label='姓名 / 常用名', max_length=80)
    password = forms.CharField(
        label='设置密码',
        min_length=10,
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password_confirm = forms.CharField(
        label='确认密码',
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    consent = forms.BooleanField(label='同意使用学号建立本网站账户，并按活动规则填写参赛信息')

    def clean_student_id(self):
        value = super().clean_student_id()
        if StudentAccount.objects.filter(student_id=value).exists():
            raise ValidationError('该学号已注册，请直接登录。')
        return value

    def clean(self):
        data = super().clean()
        password = data.get('password')
        if password:
            validate_password(password, User(fullname=data.get('name', '')))
            if password.isdigit() or password.casefold() == data.get('student_id', '').casefold():
                self.add_error('password', '密码不能是纯数字或与学号相同。')
        if password and password != data.get('password_confirm'):
            self.add_error('password_confirm', '两次密码不一致。')
        return data


def validate_zip_directory(upload):
    """Bound ZIP metadata before ZipFile allocates its full entry list."""
    upload.seek(max(0, upload.size - 65557))
    tail = upload.read(65557)
    end = tail.rfind(b'PK\x05\x06')
    if end < 0 or len(tail) - end < 22:
        raise ValidationError('压缩包目录不完整。')
    _, disk, directory_disk, count_disk, count, directory_size, directory_offset, comment_size = struct.unpack_from(
        '<4s4H2LH', tail, end
    )
    if (
        disk
        or directory_disk
        or count_disk != count
        or count > 20000
        or directory_size > 4 * 1024 * 1024
        or end + 22 + comment_size != len(tail)
        or (end >= 20 and tail[end - 20 : end - 16] == b'PK\x06\x07')
        or directory_offset + directory_size > upload.size
    ):
        raise ValidationError('压缩包目录过大，或使用了不支持的分卷 / ZIP64 格式。请导出普通 ZIP。')
    directory_end = upload.size - len(tail) + end
    if directory_size > directory_end:
        raise ValidationError('压缩包目录无效。')
    upload.seek(directory_end - directory_size)
    seen = 0
    while upload.tell() < directory_end:
        header = upload.read(46)
        if len(header) != 46 or header[:4] != b'PK\x01\x02':
            raise ValidationError('压缩包目录无效。')
        name_size, extra_size, entry_comment_size = struct.unpack_from('<3H', header, 28)
        upload.seek(name_size + extra_size + entry_comment_size, 1)
        seen += 1
        if seen > 20000 or upload.tell() > directory_end:
            raise ValidationError('压缩包文件过多或目录无效。')
    upload.seek(0)


def validate_demo_url(value):
    host = (urlsplit(value).hostname or '').lower().rstrip('.')
    if host == 'localhost' or host.endswith('.localhost'):
        raise ValidationError('localhost 无法供其他人访问。请提供可访问地址，或上传录屏并说明现场演示方式。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if address.is_loopback or address.is_unspecified or address.is_link_local:
        raise ValidationError('请勿填写本机或链路本地地址。校园内网地址请同时说明网络接入方式。')


class ParticipantForm(forms.ModelForm):
    consent = forms.BooleanField(label='同意组委会为报名、联络、评审和证书使用上述信息')
    keywords = forms.MultipleChoiceField(
        label='个人标签（数量不限）',
        choices=KEYWORD_CHOICES,
        required=False,
        widget=CategorizedTags,
    )
    custom_keywords = TagsField(
        label='自定义标签', required=False, help_text='用逗号或换行分隔，数量不限，每个最多 40 字。'
    )

    def __init__(self, *args, hackathon=None, **kwargs):
        super().__init__(*args, **kwargs)
        hackathon = hackathon or (self.instance.hackathon if self.instance.hackathon_id else None)
        self.fields['unavailable_slots'] = UnavailableHoursField(
            hackathon=hackathon,
            label='无法到场的时段（两天，精确到小时）',
            help_text='圈选无法到场的小时，可拖动连续选择，也可用键盘逐格勾选。仅工作人员可见。',
        )
        self.initial['unavailable_slots'] = self.instance.unavailable_slots
        self.initial['keywords'] = [tag for tag in self.instance.keywords if tag in KEYWORDS]
        self.initial['custom_keywords'] = ', '.join(tag for tag in self.instance.keywords if tag not in KEYWORDS)
        self.fields['availability'].label = '其他特殊安排（仅工作人员可见）'

    def clean(self):
        data = super().clean()
        data['keywords'] = list(dict.fromkeys(data.get('keywords', []) + data.get('custom_keywords', [])))
        return data

    class Meta:
        model = Participant
        fields = [
            'name',
            'school',
            'contact',
            'keywords',
            'tagline',
            'skills',
            'bio',
            'looking_for_team',
            'availability',
            'unavailable_slots',
            'certificate_name',
            'consent',
        ]
        widgets = {'bio': forms.Textarea(attrs={'rows': 3})}


class TeamForm(forms.ModelForm):
    version = forms.IntegerField(min_value=1, required=False, widget=forms.HiddenInput)
    track = forms.ModelChoiceField(label='赛道', queryset=None)

    class Meta:
        model = Team
        fields = ['name', 'track', 'intro', 'needs', 'recruiting']
        widgets = {'intro': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, hackathon, **kwargs):
        super().__init__(*args, **kwargs)
        self.hackathon = hackathon
        self.fields['track'].queryset = Track.objects.filter(hackathon=hackathon)
        self.fields['version'].initial = self.instance.roster_version if self.instance.created else 1

    def clean_name(self):
        value = self.cleaned_data['name'].strip()
        if Team.objects.filter(hackathon=self.hackathon, name__iexact=value).exclude(pk=self.instance.pk).exists():
            raise ValidationError('本次活动已有同名队伍，请换一个名称。')
        return value


class ProjectForm(forms.ModelForm):
    client_clicked_at = forms.DateTimeField(required=False, widget=forms.HiddenInput)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    class Meta:
        model = Project
        fields = [
            'title',
            'summary',
            'description',
            'repository_url',
            'demo_url',
            'backup_url',
            'demo_instructions',
            'checklist',
            'public_consent',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6}),
            'demo_instructions': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].initial = self.instance.revision
        for name in ('demo_url', 'backup_url'):
            self.fields[name].validators.append(validate_demo_url)


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = ['kind', 'file']
        labels = {'file': '上传文件（最大 512 MiB；海报最大 20 MiB）'}

    def clean(self):
        data = super().clean()
        upload, kind = data.get('file'), data.get('kind')
        if not upload or not kind:
            return data
        limit = POSTER_MAX_SIZE if kind == 'poster' else MAX_UPLOAD_SIZE
        if not upload.size or upload.size > limit:
            raise ValidationError(f'请选择非空且不超过 {limit // (1024 * 1024)} MiB 的文件。')
        suffix = Path(upload.name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS[kind]:
            raise ValidationError('此材料类型不支持该格式。海报用 PNG/JPG/WebP；文稿用 PDF/PPTX；录屏用 MP4/WebM。')
        signature = upload.read(16)
        upload.seek(0)
        try:
            if kind == 'poster':
                with Image.open(upload) as picture:
                    picture.verify()
            elif suffix == '.pdf' and not signature.startswith(b'%PDF-'):
                raise ValidationError('文件不是有效的 PDF。')
            elif suffix in {'.pptx', '.zip'}:
                validate_zip_directory(upload)
                with zipfile.ZipFile(upload) as archive:
                    entries = archive.infolist()
                    if len(entries) > 20000 or sum(entry.file_size for entry in entries) > 2 * 1024 * 1024 * 1024:
                        raise ValidationError('压缩包解压后过大或文件过多。')
                    if any(
                        '..' in Path(entry.filename).parts
                        or entry.filename.startswith(('/', '\\'))
                        or '\\' in entry.filename
                        for entry in entries
                    ):
                        raise ValidationError('压缩包包含不安全的文件路径。')
                    if suffix == '.pptx' and 'ppt/presentation.xml' not in archive.namelist():
                        raise ValidationError('文件不是有效的 PPTX。')
            elif suffix == '.mp4' and signature[4:8] != b'ftyp':
                raise ValidationError('文件不是有效的 MP4。')
            elif suffix == '.webm' and not signature.startswith(b'\x1aE\xdf\xa3'):
                raise ValidationError('文件不是有效的 WebM。')
        except (UnidentifiedImageError, OSError, zipfile.BadZipFile, Image.DecompressionBombError) as error:
            raise ValidationError('文件损坏或格式不匹配，请重新导出后上传。') from error
        finally:
            upload.seek(0)
        return data


class UploadStartForm(forms.Form):
    id = forms.UUIDField()
    kind = forms.ChoiceField(choices=Asset.Kind.choices)
    filename = forms.CharField(max_length=255)
    size = forms.IntegerField(min_value=1, max_value=MAX_UPLOAD_SIZE)
    checksum = forms.RegexField(r'^[0-9a-f]{64}$', required=False)
    client_clicked_at = forms.DateTimeField(required=False)

    def clean(self):
        data = super().clean()
        kind, filename, size = data.get('kind'), data.get('filename'), data.get('size')
        if kind and filename and Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS[kind]:
            raise ValidationError('所选材料类型不支持此文件格式。')
        if kind == 'poster' and size and size > POSTER_MAX_SIZE:
            raise ValidationError('海报不能超过 20 MiB。')
        if filename:
            data['filename'] = filename.replace('\\', '/').split('/')[-1]
        return data


class ScoreForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)
    scoring_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)

    def __init__(self, *args, hackathon=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = self.instance.revision or 0
        self.fields['scoring_revision'].initial = hackathon.scoring_revision if hackathon else 0
        self.fields['feedback'].widget.attrs['rows'] = 3
        rule_weights = weights(hackathon) if hackathon else {name: default for name, label, default in CRITERIA}
        for name, label, default in CRITERIA:
            self.fields[name].label = f'{label} · {rule_weights[name]}%'
            self.fields[name].widget.attrs['data-weight'] = rule_weights[name]

    class Meta:
        model = Score
        fields = [
            'innovation',
            'implementation',
            'impact',
            'presentation',
            'breakthrough',
            'creativity',
            'feedback',
            'final',
        ]
        widgets = {
            name: forms.NumberInput(attrs={'min': 0, 'max': 10})
            for name in ['innovation', 'implementation', 'impact', 'presentation']
        }


class ScoringRulesForm(forms.ModelForm):
    class Meta:
        model = Hackathon
        fields = [
            'innovation_weight',
            'implementation_weight',
            'impact_weight',
            'presentation_weight',
            'score_aggregation',
            'trim_extremes',
        ]

    def clean(self):
        data = super().clean()
        fields = [f'{name}_weight' for name, label, default in CRITERIA]
        if all(field in data for field in fields) and sum(data[field] for field in fields) != 100:
            raise ValidationError('四项权重之和必须为 100%。')
        return data


class JudgeAccountForm(forms.Form):
    name = forms.CharField(label='评委姓名', max_length=80)
    email = forms.EmailField(label='登录邮箱', max_length=190)
    password = forms.CharField(label='登录密码', min_length=10, max_length=128, strip=False, widget=forms.PasswordInput)
    password_confirm = forms.CharField(label='确认密码', max_length=128, strip=False, widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('此邮箱已有账户，请在工作人员页面分配评委角色，无需重新创建。')
        return email

    def clean(self):
        data = super().clean()
        if data.get('password'):
            validate_password(data['password'], User(email=data.get('email', ''), fullname=data.get('name', '')))
            if data['password'] != data.get('password_confirm'):
                self.add_error('password_confirm', '两次密码不一致。')
        return data


class IntakeForm(ProjectForm):
    reason = forms.CharField(
        label='补录原因与异常发生时间（写入操作日志）',
        min_length=8,
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
    )


class IntakeAssetForm(AssetForm):
    reason = forms.CharField(label='补录原因与异常发生时间（写入操作日志）', min_length=8, max_length=1000)


class SettingsForm(forms.ModelForm):
    starts_at = forms.DateTimeField(
        label='活动开始', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
    )
    ends_at = forms.DateTimeField(
        label='活动结束', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['starts_at'].initial = self.instance.event.date_from
        self.fields['ends_at'].initial = self.instance.event.date_to

    def clean(self):
        data = super().clean()
        start, end = data.get('starts_at'), data.get('ends_at')
        if start and end and end <= start:
            self.add_error('ends_at', '活动结束必须晚于开始。')
        return data

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            instance.event.date_from = self.cleaned_data['starts_at']
            instance.event.date_to = self.cleaned_data['ends_at']
            instance.event.save(update_fields=['date_from', 'date_to'])
        return instance

    class Meta:
        model = Hackathon
        fields = [
            'name',
            'tagline',
            'description',
            'venue',
            'contact',
            'registration_deadline',
            'team_freeze_at',
            'soft_deadline',
            'submission_deadline',
            'freeze_at',
            'team_size',
            'demo_seconds',
            'transition_seconds',
            'is_public',
            'gallery_open',
            'judging_open',
            'results_published',
        ]
        widgets = {
            name: forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
            for name in ['registration_deadline', 'team_freeze_at', 'soft_deadline', 'submission_deadline', 'freeze_at']
        }


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'body', 'pinned', 'audience']


class CommunityPostForm(forms.ModelForm):
    class Meta:
        model = CommunityPost
        fields = ['kind', 'title', 'body', 'invitation', 'demo_url', 'is_public', 'is_open']
        widgets = {'body': forms.Textarea(attrs={'rows': 5})}


class CommunityResponseForm(forms.ModelForm):
    class Meta:
        model = CommunityResponse
        fields = ['body', 'private']
        widgets = {'body': forms.Textarea(attrs={'rows': 3})}


class BonusRuleForm(forms.ModelForm):
    class Meta:
        model = BonusRule
        fields = ['title', 'description', 'points', 'active', 'position']


class ResourceForm(forms.ModelForm):
    class Meta:
        model = Resource
        fields = ['title', 'category', 'summary', 'body', 'url', 'minutes', 'required', 'position']


class TrackForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = ['name', 'description']


class ScheduleForm(forms.ModelForm):
    class Meta:
        model = ScheduleItem
        fields = ['title', 'kind', 'starts_at', 'ends_at', 'location', 'description']
        widgets = {
            name: forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
            for name in ['starts_at', 'ends_at']
        }


class StaffForm(forms.Form):
    email = forms.CharField(label='已注册的学号或账户邮箱', max_length=190)
    role = forms.ChoiceField(label='角色', choices=StaffAssignment._meta.get_field('role').choices)

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        try:
            if '@' in email:
                self.staff_user = User.objects.get(email__iexact=email, is_active=True)
            else:
                self.staff_user = User.objects.get(student_account__student_id=email.upper(), is_active=True)
        except User.DoesNotExist as error:
            raise ValidationError('请先让对方注册 Eventyay 账户，再添加角色。') from error
        return email


class TaskForm(forms.ModelForm):
    class Meta:
        model = VolunteerTask
        fields = ['title', 'description', 'location', 'owner', 'backup', 'due_at', 'approved_minutes']
        widgets = {'due_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')}

    def __init__(self, *args, hackathon, **kwargs):
        super().__init__(*args, **kwargs)
        volunteers = StaffAssignment.objects.filter(hackathon=hackathon, role='volunteer').values('user_id')
        for field in ['owner', 'backup']:
            self.fields[field].queryset = User.objects.filter(pk__in=volunteers)


class SupportForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ['category', 'subject', 'body']


class ReplyForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ['state', 'reply']


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['state', 'review_note', 'equipment_checked', 'award']

    def clean(self):
        data = super().clean()
        if data.get('state') == Project.State.CHANGES and not data.get('review_note', '').strip():
            self.add_error('review_note', '请说明具体要补充什么。')
        if self.instance.state != Project.State.DRAFT and data.get('state') == Project.State.DRAFT:
            self.add_error('state', '已提交作品请使用“需要补充”状态，保留提交记录。')
        return data
