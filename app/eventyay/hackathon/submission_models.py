"""Material policy and immutable, numbered eligibility captures."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from eventyay.hackathon.models import EventRecord


class MaterialPolicy(EventRecord):
    enabled = models.BooleanField('启用初评与上场材料要求', default=True)
    require_preliminary_video = models.BooleanField('正式提交必须有初评短视频', default=True)
    preliminary_max_seconds = models.PositiveIntegerField(
        '初评视频最长秒数（留空表示尚未定案）', null=True, blank=True, default=60
    )
    require_final_material = models.BooleanField('正式提交必须选择 PDF 或现场视频', default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon'], name='hack_material_policy')]


class SubmissionSnapshot(EventRecord):
    version = models.PositiveIntegerField()
    captured_at = models.DateTimeField()
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    manifest = models.JSONField()
    sha256 = models.CharField(max_length=64)
    reason = models.CharField(max_length=500)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'version'], name='hack_submission_snapshot')]
        ordering = ['-version']

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError('资格材料快照不可覆盖，请生成新版本。')
        super().save(*args, **kwargs)
