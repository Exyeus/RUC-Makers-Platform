"""Organizer page for judge seats and the judge-facing sign-in by link."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from eventyay.base.models import User
from eventyay.hackathon import judge_links
from eventyay.hackathon.accounts import finish_login
from eventyay.hackathon.models import StaffAssignment
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url


FRESH_LINKS = 'judge_links_fresh'


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def manage(request):
    h = request.hackathon
    if request.method == 'POST':
        operation = request.POST.get('operation', '')
        fresh = []
        try:
            if operation == 'create':
                created = judge_links.create_seats(h, request.user, request.POST.get('names', '').splitlines())
                fresh = [{'name': item['user'].get_display_name(), 'url': item['url']} for item in created]
                messages.success(request, f'已准备 {len(fresh)} 个评委席位。新链接只显示这一次，请现在复制发送。')
            elif operation in {'reissue', 'revoke'}:
                assignment = get_object_or_404(
                    StaffAssignment, hackathon=h, role='judge', user_id=request.POST.get('user', '')
                )
                if operation == 'reissue':
                    fresh = [
                        {
                            'name': assignment.user.get_display_name(),
                            'url': judge_links.reissue(h, request.user, assignment.user),
                        }
                    ]
                    messages.success(request, '已生成新链接，旧链接立即失效。')
                else:
                    judge_links.revoke(h, request.user, assignment.user)
                    messages.success(request, '链接已作废。已用此链接登录的设备需重新打开新链接。')
            else:
                messages.error(request, '未识别的操作，本次没有改动。')
        except ValidationError as error:
            messages.error(request, '；'.join(error.messages))
        request.session[FRESH_LINKS] = fresh
        return redirect(url(h, 'judge-links'))
    return page(
        request,
        'judge_links',
        page_title='评委席位与登录链接',
        seats=judge_links.seats(h),
        fresh=request.session.pop(FRESH_LINKS, []),
    )


@event_view(auth_shell=True, admission_public=True)
@require_http_methods(['GET', 'POST'])
def sign_in(request):
    """GET only shows whose seat this is, so link previews in chat apps never sign anyone in."""
    h = request.hackathon
    token = request.POST.get('token') or request.GET.get('token', '')
    invite = judge_links.resolve(h, token)
    if request.method == 'POST' and invite:
        judge_links.record_use(h, invite)
        return finish_login(request, invite.user, keep_logged_in=True, destination=url(h, 'judging'))
    current = request.user if request.user.is_authenticated else None
    switching = bool(invite and current and current.pk != invite.user.pk)
    return render(
        request,
        'hackathon/judge_link.html',
        {
            'hackathon': h,
            'now': timezone.now(),
            'invite': invite,
            'token': token if invite else '',
            'switching': switching,
            'current': current if isinstance(current, User) else None,
        },
        status=200 if invite else 404,
    )
