"""Verified recovery addresses, subscriptions and per-event admission grants."""

from django.conf import settings
from django.db import models

from eventyay.base.models.mixins import TimestampedModel
from eventyay.hackathon.models import EventRecord


class AccountSecurity(TimestampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hackathon_security')
    verified_email = models.EmailField(max_length=190, blank=True)
    pending_email = models.EmailField(max_length=190, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    activity_updates = models.BooleanField('接收活动动态', default=False)
    future_updates = models.BooleanField('接收后续活动信息', default=False)


class AccountActionToken(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=20, choices=[('verify', '验证邮箱'), ('reset', '重置密码')])
    digest = models.CharField(max_length=64, unique=True)
    auth_hash = models.CharField(max_length=64, default='')
    email = models.EmailField(max_length=190)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class AdmissionGrant(EventRecord):
    student_id = models.CharField('学号', max_length=32)
    email = models.EmailField('原报名邮箱', max_length=190, blank=True)
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='hackathon_grants'
    )
    approved = models.BooleanField('通过选拔', default=True)
    reason = models.CharField('核验 / 更正原因（仅主办方可见）', max_length=500, blank=True)
    # Columns of the organizer's roster export (飞书): kept for matching and team setup, never shown publicly.
    name = models.CharField('姓名', max_length=80, blank=True)
    college = models.CharField('学院与年级', max_length=120, blank=True)
    wechat = models.CharField('微信号', max_length=80, blank=True)
    teammates = models.CharField('意向队友', max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'student_id'], name='hack_admission_student')]


class UpdateDelivery(TimestampedModel):
    hackathon = models.ForeignKey('hackathon.Hackathon', on_delete=models.PROTECT)
    campaign = models.CharField(max_length=100)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'campaign', 'user'], name='hack_update_delivery')]


class JudgeInvite(EventRecord):
    """A named judge seat's sign-in link. Only the SHA-256 of the token is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hackathon_judge_invites')
    digest = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created']
