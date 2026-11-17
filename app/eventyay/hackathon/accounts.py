import hashlib
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventyay.base.auth import get_auth_backends
from eventyay.base.models import User
from eventyay.eventyay_common.views.auth import process_login
from eventyay.hackathon import identity
from eventyay.hackathon.account_models import AccountSecurity
from eventyay.hackathon.forms import (
    OrganizerLoginForm,
)
from eventyay.hackathon.identity_forms import UnifiedLoginForm, UnifiedSignupForm
from eventyay.hackathon.models import Participant, StaffAssignment, StudentAccount
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.views import landing_context, page, url
from eventyay.multidomain.middlewares import get_cookie_domain


logger = logging.getLogger(__name__)


def login_limited(request, identifier_field='student_id'):
    """Shared Redis counters: bound password attempts per IP and per identifier."""
    raw_identifier = (
        request.POST.get(identifier_field) or request.POST.get('student_id') or request.POST.get('email') or ''
    ).strip()
    if '@' in raw_identifier:
        account_user = User.objects.filter(email__iexact=raw_identifier).first()
    else:
        student = StudentAccount.objects.select_related('user').filter(student_id=raw_identifier.upper()).first()
        account_user = student.user if student else None
    canonical = f'user:{account_user.pk}' if account_user else raw_identifier.upper()
    identities = [
        (request.META.get('REMOTE_ADDR', ''), 600),
        (canonical, 15),
    ]
    limited = False
    for counter_identity, limit in identities:
        key = 'hackathon:login:' + hashlib.sha256(counter_identity.encode()).hexdigest()
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
    if request.hackathon.admission_required and not identity.admitted(user, request.hackathon):
        return url(request.hackathon, 'home')
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
    if (
        request.hackathon.admission_required
        and not is_organizer(user, request.hackathon)
        and not StaffAssignment.objects.filter(hackathon=request.hackathon, user=user).exists()
        and not identity.admitted(user, request.hackathon)
    ):
        destination = url(request.hackathon, 'home')
    query = request.GET.copy()
    query['next'] = destination
    request.GET = query
    request.session.pop('socialauth_next_url', None)
    return process_login(request, user, keep_logged_in=keep_logged_in)


@sensitive_post_parameters('password')
@event_view(auth_shell=True, admission_public=True)
@require_http_methods(['GET', 'POST'])
def organizer_login(request, judge=False):
    context = {
        'judge_login': judge,
        'login_url': url(request.hackathon, 'judge-login' if judge else 'organizer-login'),
    }
    # A private event renders the content-free shell until the session proves this edition's access.
    shell = 'private_auth' if request.hackathon_shell else 'organizer_auth'
    if request.user.is_authenticated:
        if judge and 'judge' in request.hackathon_roles:
            return redirect(url(request.hackathon, 'judge-live'))
        if request.hackathon_organizer:
            if not judge:
                return redirect(workspace_destination(request, request.user))
        return page(
            request,
            shell,
            switch_account=True,
            workspace_url=workspace_destination(request, request.user),
            **context,
        )
    backend = get_auth_backends().get('native')
    if backend is None:
        return page(request, shell, unavailable=True, **context)
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
    response = page(request, shell, form=form, **context)
    response.status_code = status
    return response


@require_POST
@event_view(admission_public=True)
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
@event_view(admission_public=True)
@require_http_methods(['GET', 'POST'])
def student_auth(request, signup=False):
    if request.user.is_authenticated:
        return redirect(workspace_destination(request, request.user))
    if not signup and request.method == 'GET':
        return redirect(login_entry_url(request))
    form = (UnifiedSignupForm if signup else UnifiedLoginForm)(request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        valid = form.is_valid()
        candidate = form.get_user() if valid and not signup else None
        if login_limited(request, 'identifier'):
            form.add_error(None, '尝试过于频繁，请 5 分钟后重试。')
            status = 429
        elif valid:
            user = None
            if signup:
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            email=form.cleaned_data['resolved_email'],
                            password=form.cleaned_data['password'],
                            fullname=form.cleaned_data['name'],
                            locale='zh-hans',
                            timezone=request.hackathon.event.timezone,
                        )
                        if form.cleaned_data['student_id']:
                            StudentAccount.objects.create(student_id=form.cleaned_data['student_id'], user=user)
                        AccountSecurity.objects.create(
                            user=user,
                            activity_updates=form.cleaned_data['activity_updates'],
                            future_updates=form.cleaned_data['future_updates'],
                        )
                except IntegrityError:
                    user = None
                    if StudentAccount.objects.filter(student_id=form.cleaned_data['student_id']).exists():
                        form.add_error('identifier', '该学号已注册，请登录原账号。')
                    else:
                        form.add_error('email', '该邮箱已被使用，请登录原账号或联系组委会核验。')
                if user:
                    sent = identity.send_action(user, request.hackathon, 'verify')
                    messages.info(
                        request,
                        '账号已创建，请验证邮箱。注册不代表通过本届参赛筛选。'
                        + ('验证邮件已发送。' if sent else '验证邮件暂未发出，可在账号设置中重试。'),
                    )
            else:
                if candidate and candidate.check_password(form.cleaned_data['password']):
                    user = candidate
                else:
                    if not candidate:
                        make_password(form.cleaned_data['password'])
                    form.add_error(None, '学号、邮箱或密码不正确，或账号已停用。')
            if user:
                return finish_login(request, user)
    if signup:
        response = page(
            request,
            'student_auth',
            form=form,
            signup=True,
            title='注册账号',
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
