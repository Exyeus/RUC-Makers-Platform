import re

from django import forms
from django.core.exceptions import ValidationError

from eventyay.base.models import User
from eventyay.hackathon.forms import StudentPasswordResetForm
from eventyay.hackathon.models import StudentAccount


def student_identifier(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{4,32}', value):
        raise ValidationError('学号须为 4–32 位数字或英文字母。')
    return value


def normalize_identifier(value):
    value = value.strip()
    if '@' in value:
        return forms.EmailField(max_length=190).clean(value).lower()
    return student_identifier(value)


class IdentifierForm(forms.Form):
    identifier = forms.CharField(
        label='学号或邮箱',
        max_length=190,
        widget=forms.TextInput(attrs={'autocomplete': 'username', 'autocapitalize': 'none'}),
    )

    def __init__(self, data=None, *args, **kwargs):
        # Existing bookmarked forms and clients continue to resolve to the same account.
        if data is not None and not data.get('identifier'):
            data = data.copy()
            data['identifier'] = data.get('student_id') or data.get('email') or ''
        super().__init__(data, *args, **kwargs)

    def clean_identifier(self):
        return normalize_identifier(self.cleaned_data['identifier'])

    def get_user(self):
        value = self.cleaned_data['identifier']
        if '@' in value:
            return User.objects.filter(
                email__iexact=value, is_active=True, is_spam=False, auth_backend='native'
            ).first()
        account = StudentAccount.objects.select_related('user').filter(student_id=value).first()
        if account and account.user.is_active and not account.user.is_spam and account.user.auth_backend == 'native':
            return account.user
        return None


class UnifiedLoginForm(IdentifierForm):
    password = forms.CharField(
        label='密码',
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )


class UnifiedSignupForm(IdentifierForm, StudentPasswordResetForm):
    email = forms.EmailField(
        label='绑定邮箱',
        max_length=190,
        required=False,
        help_text='学号注册时必填，用于邮箱验证和密码找回；使用邮箱注册时无需重复填写。',
    )
    name = forms.CharField(label='姓名 / 常用名', max_length=80)
    activity_updates = forms.BooleanField(label='接收本平台活动动态邮件（可随时退订）', required=False)
    future_updates = forms.BooleanField(label='接收后续活动与招募信息（可随时退订）', required=False)
    consent = forms.BooleanField(label='同意建立本站账号，并为活动组织使用所填信息；注册不代表通过参赛筛选')
    field_order = [
        'identifier',
        'email',
        'name',
        'password',
        'password_confirm',
        'activity_updates',
        'future_updates',
        'consent',
    ]

    def clean(self):
        data = super().clean()
        value = data.get('identifier', '')
        email = (value if '@' in value else data.get('email', '')).strip().lower()
        if value and not email:
            self.add_error('email', '学号注册必须绑定可用邮箱。')
        elif email and User.objects.filter(email__iexact=email).exists():
            self.add_error('identifier', '该邮箱已被使用，请登录原账号后绑定学号，或使用找回密码。')
        if value and '@' not in value and StudentAccount.objects.filter(student_id=value).exists():
            self.add_error('identifier', '该学号已注册，请登录或找回密码；绑定有误请联系组委会核验。')
        if value and data.get('password', '').casefold() == value.casefold():
            self.add_error('password', '密码不能与学号或邮箱相同。')
        data['resolved_email'] = email
        data['student_id'] = value if value and '@' not in value else ''
        return data


class AccountPreferencesForm(forms.Form):
    activity_updates = forms.BooleanField(label='接收本平台活动动态邮件', required=False)
    future_updates = forms.BooleanField(label='接收后续活动与招募信息', required=False)


class ChangeEmailForm(forms.Form):
    email = forms.EmailField(label='新邮箱', max_length=190)
    password = forms.CharField(label='当前密码', strip=False, max_length=128, widget=forms.PasswordInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        value = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=value).exclude(pk=self.user.pk).exists():
            raise ValidationError('该邮箱已被其他账号使用。')
        return value

    def clean_password(self):
        value = self.cleaned_data['password']
        if not self.user.check_password(value):
            raise ValidationError('当前密码不正确。')
        return value


class StudentBindingForm(forms.Form):
    student_id = forms.CharField(label='绑定学号', max_length=32)

    def clean_student_id(self):
        value = self.cleaned_data.get('student_id', '')
        return student_identifier(value) if value else ''


class AdmissionCSVForm(forms.Form):
    file = forms.FileField(label='通过名单 CSV', help_text='UTF-8 CSV，表头 student_id,email,approved；先预览再导入。')

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > 256 * 1024:
            raise ValidationError('CSV 文件不能超过 256 KB。')
        return upload


class AdmissionGrantForm(forms.Form):
    student_id = forms.CharField(label='学号', max_length=32)
    email = forms.EmailField(label='原报名邮箱', max_length=190, required=False)
    assigned_email = forms.EmailField(
        label='人工核验后指定的账号邮箱',
        max_length=190,
        required=False,
        help_text='仅在已核实报名身份后填写；该账号仍须验证邮箱，且绑定学号必须一致。',
    )
    approved = forms.BooleanField(label='通过选拔（取消勾选即撤销）', required=False, initial=True)
    reason = forms.CharField(label='核验 / 更正原因', max_length=500)

    def clean_student_id(self):
        return student_identifier(self.cleaned_data['student_id'])

    def clean(self):
        data = super().clean()
        data['email'] = data.get('email', '').strip().lower()
        assigned = data.get('assigned_email', '').strip().lower()
        data['assigned_user'] = None
        if assigned:
            user = User.objects.filter(email__iexact=assigned, is_active=True, is_spam=False).first()
            if not user:
                self.add_error('assigned_email', '未找到可用账号，请让选手先注册。')
            elif not StudentAccount.objects.filter(user=user, student_id=data.get('student_id')).exists():
                self.add_error('assigned_email', '该账号绑定的学号不一致，请先核验并处理学号绑定。')
            else:
                data['assigned_user'] = user
        if data.get('approved') and not (data.get('email') or data.get('assigned_user')):
            self.add_error('email', '请填写原报名邮箱，或经人工核验后指定账号。')
        return data
