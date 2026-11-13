"""Account mail and admission checks shared by every event entry point."""

import hashlib
import logging
import secrets
import smtplib
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from eventyay.base.models import User
from eventyay.hackathon.account_models import AccountActionToken, AccountSecurity, AdmissionGrant
from eventyay.hackathon.models import StudentAccount


logger = logging.getLogger(__name__)
DENIED = '您的学号未通过本届黑客松活动筛选，暂无法进入活动主页。如有疑问，请联系组委会。'


def email_verified(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and not user.is_spam
        and AccountSecurity.objects.filter(user=user, verified_email__iexact=user.email, verified_at__isnull=False)
        .exclude(verified_email='')
        .exists()
    )


def admitted(user, hackathon):
    if not user.is_authenticated or not user.is_active or user.is_spam:
        return False
    if not hackathon.admission_required:
        return True
    if not email_verified(user):
        return False
    account = StudentAccount.objects.filter(user=user).first()
    if not account:
        return False
    grant = AdmissionGrant.objects.filter(hackathon=hackathon, student_id=account.student_id, approved=True).first()
    if not grant:
        return False
    # A manual assignment is authoritative, including after a recovery-email change.
    if grant.assigned_user_id:
        return grant.assigned_user_id == user.pk
    return bool(grant.email and grant.email.casefold() == user.email.casefold())


def public_status(user, hackathon):
    if not user.is_authenticated:
        return ''
    if not email_verified(user):
        return '请先验证邮箱；注册账号不代表已取得本届黑客松参赛资格。'
    if not StudentAccount.objects.filter(user=user).exists():
        return '请在账号设置中绑定学号，系统将按本届通过名单核验参赛资格。'
    return '' if admitted(user, hackathon) else DENIED


def absolute_url(path):
    # Use the configured canonical origin, never a user-supplied Host header.
    return settings.SITE_URL.rstrip('/') + path


def limited(namespace, value, *, seconds=60):
    key = 'hackathon:account:' + namespace + ':' + hashlib.sha256(str(value).encode()).hexdigest()
    return not cache.add(key, True, timeout=seconds)


def send_action(user, hackathon, purpose):
    """Return False on cooldown; mail failures leave the user free to retry."""
    if not user.is_active or user.is_spam or (purpose == 'reset' and not email_verified(user)):
        return False
    cooldown = 'hackathon:account:mail:' + hashlib.sha256(f'{purpose}:{user.pk}'.encode()).hexdigest()
    if not cache.add(cooldown, True, timeout=60):
        return False
    target = (user.email or '').strip().lower()
    if purpose == 'verify':
        profile = AccountSecurity.objects.filter(user=user).first()
        if profile and profile.pending_email:
            target = profile.pending_email
    if not target:
        cache.delete(cooldown)
        return False
    token = secrets.token_urlsafe(32)
    record = AccountActionToken.objects.create(
        user=user,
        purpose=purpose,
        digest=hashlib.sha256(token.encode()).hexdigest(),
        auth_hash=hashlib.sha256(user.get_session_auth_hash().encode()).hexdigest(),
        email=target,
        expires_at=timezone.now() + timedelta(minutes=30),
    )
    route = 'verify-email' if purpose == 'verify' else 'student-password-reset-confirm'
    link = absolute_url(reverse('hackathon:' + route, kwargs={'hackathon_slug': hackathon.slug})) + '?token=' + token
    verb = '验证邮箱' if purpose == 'verify' else '重置密码'
    try:
        send_mail(
            subject=f'造物有心·黑客松｜{verb}',
            message=(
                f'请打开以下链接{verb}：\n\n{link}\n\n'
                '链接 30 分钟内有效，完成操作后失效。\n如非本人操作，请忽略此邮件。'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[target],
            fail_silently=False,
        )
    except (smtplib.SMTPException, OSError):
        record.used_at = timezone.now()
        record.save(update_fields=['used_at'])
        cache.delete(cooldown)
        # Do not log SMTP exceptions containing recipient addresses or message tokens.
        logger.error('Account action email delivery failed (purpose=%s, token_record=%s)', purpose, record.pk)
        return False
    return True


def locked_token(raw, purpose):
    """Call inside transaction.atomic; callers consume only on a successful POST."""
    if not raw or len(raw) > 200:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    owner = AccountActionToken.objects.filter(digest=digest, purpose=purpose).values_list('user_id', flat=True).first()
    if owner is None:
        return None
    # Always lock the user before token rows: concurrent tokens for one account
    # must not race a password/email change or deadlock when invalidated together.
    user = User.objects.select_for_update().get(pk=owner)
    record = (
        AccountActionToken.objects.select_for_update()
        .filter(
            digest=digest,
            purpose=purpose,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not record or not user.is_active or user.is_spam:
        return None
    record.user = user
    if not constant_time_compare(record.auth_hash, hashlib.sha256(user.get_session_auth_hash().encode()).hexdigest()):
        return None
    if record.email.casefold() != record.user.email.casefold():
        if (
            purpose != 'verify'
            or not AccountSecurity.objects.filter(user=record.user, pending_email=record.email).exists()
        ):
            return None
    if purpose == 'reset' and not email_verified(record.user):
        return None
    return record


@transaction.atomic
def verify_token(raw):
    record = locked_token(raw, 'verify')
    if not record:
        return False
    profile, _ = AccountSecurity.objects.select_for_update().get_or_create(user=record.user)
    if record.email.casefold() != record.user.email.casefold():
        record.user.email = record.email
        record.user.pw_reset_token = None
        record.user.pw_reset_time = None
        record.user.update_session_token()
        record.user.save(update_fields=['email', 'pw_reset_token', 'pw_reset_time'])
        profile.pending_email = ''
    profile.verified_email, profile.verified_at = record.email, timezone.now()
    profile.save(update_fields=['verified_email', 'verified_at', 'pending_email', 'updated'])
    AccountActionToken.objects.filter(user=record.user, purpose='verify', used_at__isnull=True).update(
        used_at=timezone.now()
    )
    return True


def unsubscribe_token(profile):
    return signing.dumps({'u': profile.user_id, 'e': profile.verified_email}, salt='hackathon.unsubscribe')


def unsubscribe_profile(raw):
    try:
        data = signing.loads(raw, salt='hackathon.unsubscribe')
        return AccountSecurity.objects.filter(user_id=data['u'], verified_email=data['e']).first()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
