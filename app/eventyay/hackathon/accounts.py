import datetime as dt
import hashlib
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventyay.base.auth import get_auth_backends
from eventyay.base.models import User
from eventyay.eventyay_common.views.auth import process_login
from eventyay.hackathon.forms import (
    OrganizerLoginForm,
    StudentLoginForm,
    StudentPasswordResetForm,
    StudentPasswordResetRequestForm,
    StudentSignupForm,
)
from eventyay.hackathon.models import Participant, StaffAssignment, StudentAccount
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.views import landing_context, page, url
from eventyay.multidomain.middlewares import get_cookie_domain


def login_limited(request, identifier_field='student_id'):
    """Shared Redis counters: bound password attempts per IP and per identifier."""
    identities = [
        (request.META.get('REMOTE_ADDR', ''), 600),
        (request.POST.get(identifier_field, '').strip().upper(), 15),
    ]
    limited = False
    for identity, limit in identities:
        key = 'hackathon:login:' + hashlib.sha256(identity.encode()).hexdigest()
        if cache.add(key, 1, timeout=300):
            count = 1
        else:
            try:
                count = cache.incr(key)
            except ValueError:
                cache.add(key, 1, timeout=300)
                count = 1
        limited = limited or count > limit
    return limited


def workspace_destination(request, user):
    if is_organizer(user, request.hackathon):
        return url(request.hackathon, 'manage')
    roles = set(StaffAssignment.objects.filter(hackathon=request.hackathon, user=user).values_list('role', flat=True))
    if 'judge' in roles:
        return url(request.hackathon, 'judging')
    if 'volunteer' in roles:
        return url(request.hackathon, 'tasks')
    registered = Participant.objects.filter(hackathon=request.hackathon, user=user).exists()
    return url(request.hackathon, 'home' if registered else 'register')


def safe_workspace_next(request, value, fallback):
    legacy_home = reverse('hackathon:legacy-workspace')
    if request.hackathon.slug == 'tongzhou26' and value and value.startswith(legacy_home):
        value = url(request.hackathon, 'home') + value.removeprefix(legacy_home)
    if (
        value
        and value.startswith(url(request.hackathon, 'home'))
        and url_has_allowed_host_and_scheme(value, allowed_hosts=None)
    ):
        return value
    return fallback


def finish_login(request, user, keep_logged_in=False, destination=None):
    destination = safe_workspace_next(
        request, request.GET.get('next'), destination or workspace_destination(request, user)
    )
    # Authentication entry points must never redirect a successful login back
    # to themselves or to a POST-only logout endpoint.
    if destination.split('?', 1)[0] in {
        url(request.hackathon, name)
        for name in ('student-login', 'student-signup', 'organizer-login', 'judge-login', 'logout')
    }:
        destination = workspace_destination(request, user)
    query = request.GET.copy()
    query['next'] = destination
    request.GET = query
    request.session.pop('socialauth_next_url', None)
    return process_login(request, user, keep_logged_in=keep_logged_in)


@sensitive_post_parameters('password')
@event_view()
@require_http_methods(['GET', 'POST'])
def organizer_login(request, judge=False):
    context = {
        'judge_login': judge,
        'login_url': url(request.hackathon, 'judge-login' if judge else 'organizer-login'),
    }
    if request.user.is_authenticated:
        if judge and 'judge' in request.hackathon_roles:
            return redirect(url(request.hackathon, 'judge-live'))
        if request.hackathon_organizer:
            if not judge:
                return redirect(workspace_destination(request, request.user))
        return page(
            request,
            'organizer_auth',
            switch_account=True,
            workspace_url=workspace_destination(request, request.user),
            **context,
        )
    backend = get_auth_backends().get('native')
    if backend is None:
        return page(request, 'organizer_auth', unavailable=True, **context)
    backend.url = backend.authentication_url(request)
    form = OrganizerLoginForm(backend=backend, request=request, data=request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        if login_limited(request, 'email'):
            form.add_error(None, '尝试过于频繁，请 5 分钟后重试。')
            status = 429
        elif form.is_valid() and form.user_cache and form.user_cache.auth_backend == backend.identifier:
            if (
                judge
                and not StaffAssignment.objects.filter(
                    hackathon=request.hackathon, user=form.user_cache, role='judge'
                ).exists()
            ):
                form.add_error(None, '此账户尚未获授本届评委权限，请联系主办方在工作人员页面分配角色。')
            else:
                return finish_login(
                    request,
                    form.user_cache,
                    form.cleaned_data.get('keep_logged_in', False),
                    destination=url(request.hackathon, 'judge-live') if judge else None,
                )
    response = page(request, 'organizer_auth', form=form, **context)
    response.status_code = status
    return response


@require_POST
@event_view()
def logout(request):
    destination = safe_workspace_next(request, request.POST.get('next'), url(request.hackathon, 'home'))
    auth_logout(request)
    response = redirect(destination)
    response.delete_cookie('sso_token', path=settings.CSRF_COOKIE_PATH, domain=get_cookie_domain(request))
    messages.success(request, '已退出登录，可以切换其他账户。')
    return response


def login_entry_url(request):
    """The landing page hosts the only student login card; keep `next` and scroll to it."""
    target = url(request.hackathon, 'home')
    if request.GET.get('next'):
        target += '?' + urlencode({'next': request.GET['next']})
    return target + '#join'


@sensitive_post_parameters('password', 'password_confirm')
@event_view()
@require_http_methods(['GET', 'POST'])
def student_auth(request, signup=False):
    if request.user.is_authenticated:
        return redirect(workspace_destination(request, request.user))
    if not signup and request.method == 'GET':
        return redirect(login_entry_url(request))
    form = (StudentSignupForm if signup else StudentLoginForm)(request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        if login_limited(request):
            form.add_error(None, '尝试过于频繁，请 5 分钟后重试。')
            status = 429
        elif form.is_valid():
            user = None
            if signup:
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            email=form.cleaned_data['email'],
                            password=form.cleaned_data['password'],
                            fullname=form.cleaned_data['name'],
                            locale='zh-hans',
                            timezone=request.hackathon.event.timezone,
                        )
                        StudentAccount.objects.create(student_id=form.cleaned_data['student_id'], user=user)
                except IntegrityError:
                    user = None
                    if StudentAccount.objects.filter(student_id=form.cleaned_data['student_id']).exists():
                        form.add_error('student_id', '该学号已注册，请直接登录。')
                    else:
                        form.add_error('email', '该邮箱已被使用，请直接登录。')
            else:
                account = (
                    StudentAccount.objects.select_related('user')
                    .filter(student_id=form.cleaned_data['student_id'])
                    .first()
                )
                if account:
                    candidate = account.user
                    if (
                        candidate.check_password(form.cleaned_data['password'])
                        and candidate.is_active
                        and not candidate.is_spam
                    ):
                        user = candidate
                else:
                    make_password(form.cleaned_data['password'])
                if not user:
                    form.add_error(None, '学号或密码不正确，或账户已停用。')
            if user:
                return finish_login(request, user)
    if signup:
        response = page(
            request,
            'student_auth',
            form=form,
            signup=True,
            title='用学号注册',
            other_url=url(request.hackathon, 'student-login'),
            email_login=url(request.hackathon, 'organizer-login')
            + ('?' + urlencode({'next': request.GET['next']}) if request.GET.get('next') else ''),
            password_reset_url=url(request.hackathon, 'student-password-reset-request'),
        )
    else:
        response = page(
            request,
            'landing',
            login_form=form,
            password_reset_url=url(request.hackathon, 'student-password-reset-request'),
            **landing_context(request),
        )
    response.status_code = status
    return response


@sensitive_post_parameters('password')
@event_view()
@require_http_methods(['GET', 'POST'])
def student_password_reset_request(request):
    """Handle password reset request - student enters their student_id."""
    if request.user.is_authenticated:
        return redirect(workspace_destination(request, request.user))

    form = StudentPasswordResetRequestForm(request.POST if request.method == 'POST' else None)

    if request.method == 'POST':
        if login_limited(request):
            form.add_error(None, '尝试过于频繁，请 5 分钟后重试。')
        elif form.is_valid():
            user = form.get_user()
            if user and user.email:
                # User has email on file, send reset link
                one_day_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
                if not (user.pw_reset_time and one_day_ago < user.pw_reset_time < dt.datetime.now(dt.UTC)):
                    # Generate reset token
                    user.pw_reset_token = get_random_string(32)
                    user.pw_reset_time = dt.datetime.now(dt.UTC)
                    user.save(update_fields=['pw_reset_token', 'pw_reset_time'])

                    # Build reset URL
                    reset_url = request.build_absolute_uri(
                        url(request.hackathon, 'student-password-reset-confirm')
                        + f'?token={user.pw_reset_token}'
                    )

                    # Send email
                    try:
                        send_mail(
                            subject='黑客松密码重置',
                            message=(
                                '你好，\n\n'
                                '请点击以下链接重置你的密码：\n\n'
                                f'{reset_url}\n\n'
                                '链接有效期为 24 小时。\n\n'
                                '如果你没有请求重置密码，请忽略此邮件。'
                            ),
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=False,
                        )
                        messages.success(request, '密码重置链接已发送到你的注册邮箱，请查收。')
                    except Exception:
                        messages.error(request, '发送邮件失败，请稍后重试或联系组织者。')
                        return page(request, 'student_password_reset_request', form=form, title='忘记密码')

                    user.log_action('eventyay.hackathon.student.password.reset.request')
                else:
                    messages.info(request, '24 小时内已请求过重置，请检查邮箱或稍后再试。')
                return redirect(login_entry_url(request))
            elif user:
                # User exists but has no email
                contact_info = request.hackathon.contact or '请在现场咨询工作人员'
                messages.warning(
                    request,
                    f'该学号未绑定邮箱，无法自动重置。请联系组织者协助，'
                    f'或先在个人设置中添加邮箱。组委会联系方式：{contact_info}'
                )
            else:
                # User not found - don't reveal this for security
                messages.warning(
                    request,
                    '如果该学号已注册且绑定了邮箱，你将收到密码重置链接。如未收到，请联系组织者。'
                )
            return redirect(login_entry_url(request))

    return page(request, 'student_password_reset_request', form=form, title='忘记密码')


@sensitive_post_parameters('password')
@event_view()
@require_http_methods(['GET', 'POST'])
def student_password_reset_confirm(request):
    """Handle password reset confirmation - student enters new password."""
    token = request.GET.get('token') or request.POST.get('token')

    if not token:
        messages.error(request, '重置链接无效，请重新申请。')
        return redirect(url(request.hackathon, 'student-password-reset-request'))

    # Find user with this token
    try:
        user = User.objects.get(
            pw_reset_token=token,
            pw_reset_time__gte=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        )
    except User.DoesNotExist:
        messages.error(request, '重置链接已过期或无效，请重新申请。')
        return redirect(url(request.hackathon, 'student-password-reset-request'))

    form = StudentPasswordResetForm(request.POST if request.method == 'POST' else None)

    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                user.set_password(form.cleaned_data['password'])
                user.pw_reset_token = None
                user.pw_reset_time = None
                user.update_session_token()
                user.save(update_fields=['password', 'pw_reset_token', 'pw_reset_time'])

            messages.success(request, '密码已重置成功，请使用新密码登录。')
            user.log_action('eventyay.hackathon.student.password.reset.complete')
            return redirect(login_entry_url(request))

    return page(request, 'student_password_reset_confirm', form=form, title='重置密码', token=token)
