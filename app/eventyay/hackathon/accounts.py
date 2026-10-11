import hashlib
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from eventyay.base.auth import get_auth_backends
from eventyay.base.models import User
from eventyay.eventyay_common.views.auth import process_login
from eventyay.hackathon.forms import OrganizerLoginForm, StudentLoginForm, StudentSignupForm
from eventyay.hackathon.models import Participant, StaffAssignment, StudentAccount
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.views import page, url
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
    destination = safe_workspace_next(request, request.POST.get('next'), url(request.hackathon, 'student-login'))
    auth_logout(request)
    response = redirect(destination)
    response.delete_cookie('sso_token', path=settings.CSRF_COOKIE_PATH, domain=get_cookie_domain(request))
    messages.success(request, '已退出登录，可以切换其他账户。')
    return response


@sensitive_post_parameters('password', 'password_confirm')
@event_view()
@require_http_methods(['GET', 'POST'])
def student_auth(request, signup=False):
    if request.user.is_authenticated:
        return redirect(workspace_destination(request, request.user))
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
                            email=None,
                            password=form.cleaned_data['password'],
                            fullname=form.cleaned_data['name'],
                            locale='zh-hans',
                            timezone=request.hackathon.event.timezone,
                        )
                        StudentAccount.objects.create(student_id=form.cleaned_data['student_id'], user=user)
                except IntegrityError:
                    user = None
                    form.add_error('student_id', '该学号已注册，请直接登录。')
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
    response = page(
        request,
        'student_auth',
        form=form,
        signup=signup,
        title='用学号注册' if signup else '学号登录',
        other_url=url(request.hackathon, 'student-login' if signup else 'student-signup'),
        email_login=url(request.hackathon, 'organizer-login')
        + ('?' + urlencode({'next': request.GET['next']}) if request.GET.get('next') else ''),
    )
    response.status_code = status
    return response
