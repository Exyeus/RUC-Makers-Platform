"""Judge seats that sign in through a personal link, for judges who have no account yet.

The organizer names the seats; each seat is a real account holding the judge role
for this edition only. Its link is the credential: it can be reissued or revoked at
any time, and every sign-in through it is audited.
"""

import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon.account_models import JudgeInvite
from eventyay.hackathon.identity import absolute_url
from eventyay.hackathon.models import StaffAssignment


SEAT_DOMAIN = 'judge-seat.invalid'


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def link_expiry(hackathon):
    end = hackathon.event.date_to or hackathon.event.date_from
    return max(end, timezone.now()) + timedelta(days=2)


def link_url(hackathon, token):
    from eventyay.hackathon.views import url

    return absolute_url(url(hackathon, 'judge-link')) + '?token=' + token


def is_seat(user):
    return user.email.endswith('@' + SEAT_DOMAIN)


def issue(hackathon, actor, user):
    """Revoke the seat's live links and return a fresh URL; call inside a transaction."""
    now = timezone.now()
    JudgeInvite.objects.filter(hackathon=hackathon, user=user, revoked_at__isnull=True).update(revoked_at=now)
    token = secrets.token_urlsafe(32)
    JudgeInvite.objects.create(
        hackathon=hackathon, user=user, digest=digest(token), created_by=actor, expires_at=link_expiry(hackathon)
    )
    services.audit(hackathon, actor, '生成评委登录链接', user.pk, user.get_display_name())
    return link_url(hackathon, token)


@transaction.atomic
def create_seats(hackathon, actor, names):
    h = services.lock_event(hackathon)
    if h.results_published:
        raise ValidationError('成绩已公布，评委名单已锁定。')
    cleaned = []
    for raw in names:
        name = ' '.join(raw.split())
        if not name:
            continue
        if len(name) > 80:
            raise ValidationError(f'评委姓名过长：{name[:20]}…')
        if name not in cleaned:
            cleaned.append(name)
    if not cleaned:
        raise ValidationError('请至少填写一位评委姓名，每行一位。')
    existing = {
        assignment.user.get_display_name(): assignment.user
        for assignment in StaffAssignment.objects.filter(hackathon=h, role='judge').select_related('user')
        if is_seat(assignment.user)
    }
    links = []
    for name in cleaned:
        user = existing.get(name)
        if user is None:
            user = User.objects.create_user(
                email=f'judge-{secrets.token_hex(6)}@{SEAT_DOMAIN}',
                password=None,
                fullname=name,
                locale='zh-hans',
                timezone=h.event.timezone,
            )
            StaffAssignment.objects.create(hackathon=h, user=user, role='judge')
            services.audit(h, actor, '创建评委席位', user.pk, name)
        links.append({'user': user, 'url': issue(h, actor, user)})
    return links


@transaction.atomic
def reissue(hackathon, actor, user):
    h = services.lock_event(hackathon)
    if not StaffAssignment.objects.filter(hackathon=h, user=user, role='judge').exists():
        raise ValidationError('该账户已不是本届评委。')
    return issue(h, actor, user)


@transaction.atomic
def revoke(hackathon, actor, user):
    count = JudgeInvite.objects.filter(hackathon=hackathon, user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    services.audit(hackathon, actor, '作废评委登录链接', user.pk, f'{count} 条')
    return count


def resolve(hackathon, token):
    """The live invite for a token, or None. Revoked, expired or removed seats never resolve."""
    if not token or len(token) > 100:
        return None
    invite = (
        JudgeInvite.objects.filter(hackathon=hackathon, digest=digest(token), revoked_at__isnull=True)
        .select_related('user')
        .first()
    )
    if not invite or invite.expires_at <= timezone.now() or not invite.user.is_active or invite.user.is_spam:
        return None
    if not StaffAssignment.objects.filter(hackathon=hackathon, user=invite.user, role='judge').exists():
        return None
    return invite


@transaction.atomic
def record_use(hackathon, invite):
    JudgeInvite.objects.filter(pk=invite.pk).update(last_used_at=timezone.now(), use_count=invite.use_count + 1)
    services.audit(hackathon, invite.user, '评委通过链接登录', invite.user.pk)


def seats(hackathon):
    """Every judge of this edition with the state of their latest link."""
    now = timezone.now()
    rows = []
    for assignment in (
        StaffAssignment.objects.filter(hackathon=hackathon, role='judge').select_related('user').order_by('created')
    ):
        latest = JudgeInvite.objects.filter(hackathon=hackathon, user=assignment.user).first()
        if latest is None:
            state = 'none'
        elif latest.revoked_at:
            state = 'revoked'
        elif latest.expires_at <= now:
            state = 'expired'
        else:
            state = 'live'
        rows.append({'user': assignment.user, 'seat': is_seat(assignment.user), 'invite': latest, 'state': state})
    return rows
