import csv
import io
import secrets

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


def parse_csv(upload):
    try:
        reader = csv.DictReader(io.StringIO(upload.read().decode('utf-8-sig')))
        if not reader.fieldnames or not {'student_id', 'email'} <= set(reader.fieldnames):
            raise ValidationError('CSV 必须包含 student_id 和 email 表头，可选 approved（true / false）。')
        result, students, emails = [], set(), set()
        for index, row in enumerate(reader, 2):
            if index > 3001:
                raise ValidationError('一次最多导入 3000 人。')
            state = (row.get('approved') or 'true').strip().lower()
            if state not in {'true', 'false', '1', '0'}:
                raise ValidationError(f'第 {index} 行 approved 只能为 true / false / 1 / 0。')
            form = AdmissionGrantForm(
                {
                    'student_id': row.get('student_id', ''),
                    'email': row.get('email', ''),
                    'approved': state in {'true', '1'},
                    'reason': '组委会名单导入',
                }
            )
            if not form.is_valid():
                raise ValidationError(
                    f'第 {index} 行：' + '；'.join(str(e) for errors in form.errors.values() for e in errors)
                )
            values = form.cleaned_data
            sid, email = values['student_id'], values['email']
            if sid in students or (email and email in emails):
                raise ValidationError(f'第 {index} 行学号或邮箱重复。')
            students.add(sid)
            if email:
                emails.add(email)
            result.append({'student_id': sid, 'email': email, 'approved': values['approved']})
        if not result:
            raise ValidationError('CSV 没有有效数据。')
        return result
    except (UnicodeError, csv.Error) as error:
        raise ValidationError('无法读取 CSV，请使用 UTF-8 编码。') from error


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
            rows = parse_csv(upload_form.cleaned_data['file'])
        except ValidationError as error:
            upload_form.add_error('file', error)
        else:
            request.session['admission_import'] = {
                'event': request.hackathon.pk,
                'token': secrets.token_urlsafe(24),
                'rows': rows,
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
            for row in preview['rows']:
                save_grant(request, {**row, 'assigned_user': None, 'reason': '组委会名单导入'})
        request.session.pop('admission_import', None)
        messages.success(request, f'已更新 {len(preview["rows"])} 条通过名单；其他名单记录保持不变。')
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
