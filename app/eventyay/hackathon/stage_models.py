"""A durable, private request and organizer decision for stage order changes."""

import uuid

from django.conf import settings
from django.db import models

from eventyay.hackathon.models import EventRecord
from eventyay.hackathon.stage_storage import StageEvidenceStorage, evidence_path


class StageOrderRequest(EventRecord):
    class Preference(models.TextChoices):
        EARLY = 'early', '希望靠前'
        LATE = 'late', '希望靠后'
        TIME = 'time', '有明确时间限制'

    class State(models.TextChoices):
        PENDING = 'pending', '待主办处理'
        ACCEPTED = 'accepted', '已调整安排'
        REJECTED = 'rejected', '未能调整'
        WITHDRAWN = 'withdrawn', '本人已撤回'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.ForeignKey('hackathon.Participant', on_delete=models.PROTECT, related_name='stage_requests')
    team = models.ForeignKey('hackathon.Team', on_delete=models.PROTECT, related_name='stage_requests')
    preference = models.CharField('顺序诉求', max_length=12, choices=Preference.choices)
    time_constraint = models.CharField('可接受或无法接受的时间', max_length=250, blank=True)
    reason = models.TextField('无法接受原安排的原因', max_length=2000)
    evidence = models.FileField(
        '证明文件', storage=StageEvidenceStorage(), upload_to=evidence_path, max_length=220, blank=True
    )
    evidence_name = models.CharField(max_length=190, blank=True)
    evidence_size = models.PositiveIntegerField(default=0)
    evidence_checksum = models.CharField(max_length=64, blank=True)
    evidence_url = models.URLField('可核验的证明链接', max_length=500, blank=True)
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    version = models.PositiveIntegerField(default=1)
    decision_note = models.TextField('处理说明', max_length=2000, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    applied_position = models.PositiveIntegerField(null=True, blank=True)
    decision_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['hackathon', 'participant'], condition=models.Q(state='pending'), name='hack_stage_one_pending'
            )
        ]
