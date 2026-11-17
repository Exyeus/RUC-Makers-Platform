"""Explicit, versioned judging rounds; legacy scores remain separate."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from eventyay.hackathon.models import EventRecord


class JudgingRound(EventRecord):
    class Kind(models.TextChoices):
        PRELIMINARY = 'preliminary', '初评'
        FINAL = 'final', '决赛'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    rules = models.JSONField()
    panel = models.JSONField()
    tasks = models.JSONField()
    materials = models.JSONField(default=dict)
    source_snapshot = models.ForeignKey(
        'hackathon.ResultSnapshot', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    signer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    signer_nonstudent = models.BooleanField(default=False)
    frozen_at = models.DateTimeField()
    frozen_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    is_open = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'kind', 'version'], name='hack_round_version')]
        ordering = ['kind', '-version']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = type(self).objects.get(pk=self.pk)
            locked = (
                'hackathon_id',
                'kind',
                'version',
                'name',
                'rules',
                'panel',
                'tasks',
                'materials',
                'source_snapshot_id',
                'signer_id',
                'signer_nonstudent',
                'frozen_at',
                'frozen_by_id',
            )
            if any(getattr(before, field) != getattr(self, field) for field in locked):
                raise ValidationError('本轮规则、评委、任务和材料已冻结；更正请建立新一版轮次。')
        super().save(*args, **kwargs)


class RoundScore(EventRecord):
    round = models.ForeignKey(JudgingRound, on_delete=models.PROTECT, related_name='scores')
    project = models.ForeignKey('hackathon.Project', on_delete=models.PROTECT, related_name='round_scores')
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    values = models.JSONField(default=dict)
    final = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    feedback = models.TextField(max_length=3000, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'project', 'judge'], name='hack_round_score_judge')]


class ResultSnapshot(EventRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    round = models.ForeignKey(JudgingRound, on_delete=models.PROTECT, related_name='snapshots')
    kind = models.CharField(max_length=12, choices=[('finalists', '入围名单'), ('awards', '获奖名单')])
    version = models.PositiveIntegerField()
    manifest = models.JSONField()
    sha256 = models.CharField(max_length=64)
    reason = models.TextField(max_length=2000)
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'kind', 'version'], name='hack_result_version')]
        ordering = ['-version']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = type(self).objects.get(pk=self.pk)
            locked = ('hackathon_id', 'round_id', 'kind', 'version', 'manifest', 'sha256', 'reason', 'supersedes_id')
            if any(getattr(before, field) != getattr(self, field) for field in locked) or before.signed_at:
                raise ValidationError('名单快照不可覆盖；请生成新版本并重新签认。')
        super().save(*args, **kwargs)


class FinalistContact(EventRecord):
    snapshot = models.ForeignKey(ResultSnapshot, on_delete=models.PROTECT, related_name='contacts')
    team = models.ForeignKey('hackathon.Team', on_delete=models.PROTECT)
    channel = models.CharField(max_length=100, blank=True)
    contact = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    state = models.CharField(
        max_length=12,
        default='pending',
        choices=[
            ('pending', '待联络'),
            ('sent', '已送达'),
            ('accepted', '队长已确认'),
            ('declined', '已拒绝'),
            ('expired', '窗口已过期'),
        ],
    )
    arrived = models.BooleanField(default=False)
    equipment_ready = models.BooleanField(default=False)
    note = models.TextField(max_length=2000, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['snapshot', 'team'], name='hack_finalist_contact')]
