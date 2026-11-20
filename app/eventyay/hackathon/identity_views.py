import csv
import io
import secrets
import zipfile

import openpyxl
from django import forms
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from eventyay.base.models import User
from eventyay.hackathon import identity
from eventyay.hackathon.account_models import AccountActionToken, AccountSecurity, AdmissionGrant
from eventyay.hackathon.forms import StudentPasswordResetForm
from eventyay.hackathon.identity_forms import (
    AccountPreferencesForm,
    AdmissionCSVForm,
    AdmissionGrantForm,
    AdmissionModeForm,
    ChangeEmailForm,
    IdentifierForm,
    StudentBindingForm,
    student_identifier,
)
from eventyay.hackathon.models import StudentAccount
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.services import audit


def route(request, name):
    return reverse('hackathon:' + name, kwargs={'hackathon_slug': request.hackathon.slug})


def entry(request, template, **context):
    response = render(
        request,
        'hackathon/' + template + '.html',
        {
            'hackathon': request.hackathon,
            'password_reset_url': route(request, 'student-password-reset-request'),
            **context,
        },
    )
    response['Referrer-Policy'] = 'same-origin'
    response['Cache-Control'] = 'private, no-store'
    return response


@sensitive_post_parameters('password')
@event_view('login', admission_public=True)
@require_http_methods(['GET', 'POST'])
def account(request):
    user = request.user
    profile, _ = AccountSecurity.objects.get_or_create(user=user)
    operation = request.POST.get('operation') if request.method == 'POST' else ''
    preferences = AccountPreferencesForm(
        request.POST if operation == 'preferences' else None,
        initial={'activity_updates': profile.activity_updates, 'future_updates': profile.future_updates},
    )
    binding = StudentBindingForm(request.POST if operation == 'bind-student' else None)
    email_form = ChangeEmailForm(request.POST if operation == 'change-email' else None, user=user)
    if operation == 'preferences' and preferences.is_valid():
        profile.activity_updates = preferences.cleaned_data['activity_updates']
        profile.future_updates = preferences.cleaned_data['future_updates']
        profile.save(update_fields=['activity_updates', 'future_updates', 'updated'])
        messages.success(request, '邮件订阅偏好已保存；只有验证过的邮箱才会收到推送，可随时取消。')
        return redirect(route(request, 'account'))
    if operation == 'verify-email':
        if identity.send_action(user, request.hackathon, 'verify'):
            messages.success(request, '验证邮件已发送，链接 30 分钟内有效。')
        else:
            messages.warning(request, '暂未发送验证邮件。请稍后重试；如已发送，请等待 60 秒并检查收件箱和垃圾邮件。')
        return redirect(route(request, 'account'))
    if operation == 'change-email':
        if identity.limited('change-email-attempt', user.pk, seconds=10):
            email_form.add_error(None, '操作过于频繁，请稍后重试。')
        elif email_form.is_valid():
            with transaction.atomic():
                current_user = User.objects.select_for_update().get(pk=user.pk)
                if current_user.check_password(email_form.cleaned_data['password']):
                    profile.pending_email = email_form.cleaned_data['email']
                    profile.save(update_fields=['pending_email', 'updated'])
                    identity.send_action(current_user, request.hackathon, 'verify')
                    messages.info(request, '请验证新邮箱后完成更换；原邮箱在此之前继续有效。可重新发送验证邮件。')
                    return redirect(route(request, 'account'))
                email_form.add_error('password', '密码已变更，请重新登录后再试。')
    if operation == 'bind-student' and binding.is_valid():
        value = binding.cleaned_data['student_id']
        current = StudentAccount.objects.filter(user=user).first()
        if not identity.email_verified(user):
            binding.add_error(None, '请先验证邮箱，再绑定学号。')
        elif current and current.student_id != value:
            binding.add_error(None, '已绑定的学号不能自行更换；请联系组委会核实报名身份。')
        else:
            try:
                with transaction.atomic():
                    StudentAccount.objects.get_or_create(user=user, defaults={'student_id': value})
                    audit(request.hackathon, user, '绑定账号学号', value)
            except IntegrityError:
                binding.add_error(None, '该学号已绑定其他账号，请登录原账号或联系组委会核验；系统不会自动合并账号。')
            else:
                messages.success(request, '学号已绑定；参赛资格仍以本届通过名单及原报名邮箱核验结果为准。')
                return redirect(route(request, 'account'))
    student = StudentAccount.objects.filter(user=user).first()
    return entry(
        request,
        'identity_account',
        profile=profile,
        preferences=preferences,
        binding=binding,
        email_form=email_form,
        verified=identity.email_verified(user),
        student=student,
        admission_message=''
        if request.hackathon_organizer or request.hackathon_roles
        else identity.public_status(user, request.hackathon),
        has_admission=request.hackathon_admitted,
    )


@event_view(admission_public=True)
@require_http_methods(['GET', 'POST'])
def verify_email(request):
    raw = request.POST.get('token', '') if request.method == 'POST' else request.GET.get('token', '')
    if request.method == 'POST':
        try:
            success = identity.verify_token(raw)
        except IntegrityError:
            success = False
        if success:
            messages.success(request, '邮箱验证成功。请登录并在账号设置中查看参赛资格。')
            return redirect(route(request, 'home'))
        messages.error(request, '验证链接已失效、已使用或邮箱已变更。请登录账号后重新发送。')
    return entry(request, 'identity_action', token=raw, action='verify', title='验证邮箱')


@event_view(admission_public=True)
@require_http_methods(['GET', 'POST'])
def password_reset_request(request):
    form = IdentifierForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        # Same response for absent, unverified, unavailable and throttled accounts.
        if not identity.limited('reset-ip', request.META.get('REMOTE_ADDR', ''), seconds=10):
            user = form.get_user()
            if user:
                identity.send_action(user, request.hackathon, 'reset')
        messages.info(
            request,
            (
                '如果该账号绑定了已验证邮箱，您将收到重置链接。未收到时请检查垃圾邮件或稍后重试。'
                f'人工找回请联系：{request.hackathon.contact or "组委会工作人员"}。'
                '组委会核对原报名联系方式后处理，不会仅凭学号重置密码。'
            ),
        )
        return redirect(route(request, 'home') + '#join')
    return entry(request, 'identity_action', form=form, action='reset-request', title='找回密码')


@sensitive_post_parameters('password', 'password_confirm')
@event_view(admission_public=True)
@require_http_methods(['GET', 'POST'])
def password_reset_confirm(request):
    raw = request.POST.get('token', '') if request.method == 'POST' else request.GET.get('token', '')
    form = StudentPasswordResetForm(request.POST if request.method == 'POST' else None)
    with transaction.atomic():
        record = identity.locked_token(raw, 'reset')
        valid = record is not None
        if request.method == 'POST' and valid and form.is_valid():
            user = record.user
            user.set_password(form.cleaned_data['password'])
            user.update_session_token()
            user.pw_reset_token = None
            user.pw_reset_time = None
            user.save(update_fields=['password', 'pw_reset_token', 'pw_reset_time'])
            AccountSecurity.objects.filter(user=user).update(pending_email='')
            AccountActionToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
            user.log_action('eventyay.hackathon.student.password.reset.complete')
            if request.user.is_authenticated and request.user.pk == user.pk:
                auth_logout(request)
            messages.success(request, '密码已重置，请使用学号或邮箱和新密码登录。')
            return redirect(route(request, 'home') + '#join')
    return entry(
        request,
        'identity_action',
        form=form if valid else None,
        token=raw if valid else '',
        action='reset-confirm',
        title='重置密码',
        invalid=not valid,
    )


@require_http_methods(['GET', 'POST'])
def unsubscribe(request, hackathon_slug=None):
    raw = request.POST.get('token', '') if request.method == 'POST' else request.GET.get('token', '')
    profile = identity.unsubscribe_profile(raw)
    done = False
    if profile and request.method == 'POST':
        profile.activity_updates = profile.future_updates = False
        profile.save(update_fields=['activity_updates', 'future_updates', 'updated'])
        done = True
    response = render(
        request,
        'hackathon/account_unsubscribe.html',
        {
            'token': raw,
            'invalid': not profile,
            'done': done,
        },
    )
    response['Referrer-Policy'] = 'same-origin'
    response['Cache-Control'] = 'private, no-store'
    return response


ROSTER_COLUMNS = {
    'student_id': 'student_id',
    '学号': 'student_id',
    'email': 'email',
    '邮箱': 'email',
    '原报名邮箱': 'email',
    'approved': 'approved',
    '是否通过': 'approved',
    'name': 'name',
    '姓名': 'name',
    '学院与年级': 'college',
    '学院': 'college',
    '微信号': 'wechat',
    '微信': 'wechat',
    '意向队友': 'teammates',
}
ROSTER_LIMITS = {'name': 80, 'college': 120, 'wechat': 80, 'teammates': 300}


def roster_records(upload):
    """Rows of a CSV or XLSX roster as dicts keyed by the original headers."""
    data = upload.read()
    if upload.name.lower().endswith('.xlsx'):
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as error:
            raise ValidationError('无法读取 Excel 文件，请从飞书重新导出 .xlsx 或 .csv。') from error
        values = [
            ['' if cell is None else str(cell).strip() for cell in row]
            for row in workbook.worksheets[0].iter_rows(values_only=True)
        ]
        workbook.close()
        if not values:
            raise ValidationError('表格没有内容。')
        headers = [header.strip() for header in values[0]]
        return headers, [dict(zip(headers, row)) for row in values[1:]]
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValidationError('无法识别文件编码，请导出为 UTF-8 CSV 或 .xlsx。')
    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = [header.strip() for header in (reader.fieldnames or [])]
        reader.fieldnames = headers
        return headers, list(reader)
    except csv.Error as error:
        raise ValidationError('无法读取 CSV。') from error


def parse_roster(upload):
    """Normalize an organizer roster: ``rows`` to import and ``manual`` entries needing a person.

    Accepts the platform CSV (student_id,email,approved) and the 飞书 export
    (姓名 / 学号 / 学院与年级 / 微信号 / 意向队友). Rows without a usable 学号
    never become grants: they are listed for manual verification instead.
    """
    headers, records = roster_records(upload)
    columns = {header: ROSTER_COLUMNS[header] for header in headers if header in ROSTER_COLUMNS}
    if 'student_id' not in columns.values():
        raise ValidationError('表头需要包含“学号”（或 student_id）。可选：姓名、学院与年级、微信号、意向队友、邮箱。')
    rows, manual, students = [], [], set()
    for index, record in enumerate(records, 2):
        if index > 3001:
            raise ValidationError('一次最多导入 3000 人。')
        values = {field: '' for field in ROSTER_COLUMNS.values()}
        for header, field in columns.items():
            values[field] = ' '.join(str(record.get(header) or '').split())
        if not any(values.values()):
            continue
        for field, limit in ROSTER_LIMITS.items():
            values[field] = values[field][:limit]
        label = values['name'] or values['wechat'] or f'第 {index} 行'
        if not values['student_id']:
            manual.append(
                {
                    'line': index,
                    'label': label,
                    'wechat': values['wechat'],
                    'reason': '缺少学号（校外或漏填），需人工核验',
                }
            )
            continue
        state = (values['approved'] or 'true').lower()
        if state not in {'true', 'false', '1', '0', '是', '否'}:
            manual.append(
                {'line': index, 'label': label, 'wechat': values['wechat'], 'reason': '“是否通过”只能填 是 / 否'}
            )
            continue
        try:
            sid = student_identifier(values['student_id'])
            email = forms.EmailField(max_length=190, required=False).clean(values['email']).lower()
        except ValidationError as error:
            manual.append(
                {'line': index, 'label': label, 'wechat': values['wechat'], 'reason': '；'.join(error.messages)}
            )
            continue
        if sid in students:
            manual.append(
                {'line': index, 'label': label, 'wechat': values['wechat'], 'reason': f'学号 {sid} 重复，已保留第一条'}
            )
            continue
        students.add(sid)
        rows.append(
            {
                'student_id': sid,
                'email': email,
                'approved': state in {'true', '1', '是'},
                'name': values['name'],
                'college': values['college'],
                'wechat': values['wechat'],
                'teammates': values['teammates'],
            }
        )
    if not rows and not manual:
        raise ValidationError('文件没有有效数据。')
    return {'rows': rows, 'manual': manual}


def parse_csv(upload):
    """Kept for callers of the original importer; strict rows only."""
    result = parse_roster(upload)
    if result['manual']:
        first = result['manual'][0]
        raise ValidationError(f'第 {first["line"]} 行：{first["reason"]}')
    return result['rows']


@transaction.atomic
def import_grant(request, row):
    """Update roster columns only; a re-import never clears a manual assignment or note."""
    grant = AdmissionGrant.objects.filter(hackathon=request.hackathon, student_id=row['student_id']).first()
    created = grant is None
    grant = grant or AdmissionGrant(hackathon=request.hackathon, student_id=row['student_id'], reason='组委会名单导入')
    grant.approved = row['approved']
    if row['email']:
        grant.email = row['email']
    for field in ('name', 'college', 'wechat', 'teammates'):
        if row.get(field):
            setattr(grant, field, row[field])
    grant.save()
    audit(
        request.hackathon,
        request.user,
        '导入通过名单',
        row['student_id'],
        f'approved={grant.approved}; created={created}',
    )
    return created


@transaction.atomic
def save_grant(request, values):
    grant, _ = AdmissionGrant.objects.update_or_create(
        hackathon=request.hackathon,
        student_id=values['student_id'],
        defaults={key: values.get(key) for key in ('email', 'approved', 'assigned_user', 'reason')},
    )
    audit(
        request.hackathon,
        request.user,
        '更新通过名单',
        values['student_id'],
        f'approved={grant.approved}; manual_assignment={bool(grant.assigned_user_id)}; {grant.reason}',
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def admission_manage(request):
    operation = request.POST.get('operation', '') if request.method == 'POST' else ''
    upload_form = AdmissionCSVForm(
        request.POST if operation == 'preview' else None, request.FILES if operation == 'preview' else None
    )
    grant_form = AdmissionGrantForm(request.POST if operation == 'grant' else None)
    mode_form = AdmissionModeForm(
        request.POST if operation == 'mode' else None,
        initial={'open_testing': identity.open_testing(request.hackathon)},
        prefix='mode',
    )
    if operation == 'mode' and mode_form.is_valid():
        enabled = mode_form.cleaned_data['open_testing']
        request.hackathon.admission_required = True
        request.hackathon.save(update_fields=['admission_required', 'updated'])
        request.hackathon.event.settings.set('hackathon_open_testing', enabled)
        audit(
            request.hackathon,
            request.user,
            '切换活动准入模式',
            request.hackathon.slug,
            f'open_testing={enabled}; {mode_form.cleaned_data["reason"]}',
        )
        messages.success(request, '已开放测试，注册账号可进入活动。' if enabled else '已恢复通过名单筛选准入。')
        return redirect(route(request, 'admission-manage'))

    if operation == 'preview' and upload_form.is_valid():
        try:
            parsed = parse_roster(upload_form.cleaned_data['file'])
        except ValidationError as error:
            upload_form.add_error('file', error)
        else:
            known = set(
                AdmissionGrant.objects.filter(
                    hackathon=request.hackathon, student_id__in=[row['student_id'] for row in parsed['rows']]
                ).values_list('student_id', flat=True)
            )
            request.session['admission_import'] = {
                'event': request.hackathon.pk,
                'token': secrets.token_urlsafe(24),
                'rows': parsed['rows'],
                'manual': parsed['manual'],
                'updates': len(known),
            }
    if operation == 'apply':
        preview = request.session.get('admission_import', {})
        if (
            preview.get('event') != request.hackathon.pk
            or not constant_time_compare(preview.get('token', ''), request.POST.get('preview_token', ''))
            or not preview.get('rows')
        ):
            return HttpResponseBadRequest('预览已失效，请重新上传名单。')
        with transaction.atomic():
            created = sum(import_grant(request, row) for row in preview['rows'])
        request.session.pop('admission_import', None)
        messages.success(
            request,
            f'已导入 {len(preview["rows"])} 条：新增 {created} 条，更新 {len(preview["rows"]) - created} 条；'
            '名单外的记录与人工指定账号保持不变。',
        )
        return redirect(route(request, 'admission-manage'))
    if operation == 'grant' and grant_form.is_valid():
        save_grant(request, grant_form.cleaned_data)
        messages.success(request, '资格已更新，下一次请求立即按新结果鉴权。')
        return redirect(route(request, 'admission-manage'))
    preview = request.session.get('admission_import', {})
    if preview.get('event') != request.hackathon.pk:
        preview = {}
    # The organizer-only page is intentionally separate from the public account shell.
    from eventyay.hackathon.views import page

    return page(
        request,
        'admission_manage',
        title='通过名单与准入',
        upload_form=upload_form,
        grant_form=grant_form,
        mode_form=mode_form,
        open_testing=identity.open_testing(request.hackathon),
        preview=preview,
        grants=AdmissionGrant.objects.filter(hackathon=request.hackathon).order_by('student_id'),
    )
