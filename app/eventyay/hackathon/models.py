import secrets
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from django_scopes import ScopedManager

from eventyay.base.models.mixins import TimestampedModel
from eventyay.hackathon.scoring import weighted_total


def invitation_code():
    return secrets.token_urlsafe(12)


@deconstructible
class PrivateStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=Path(settings.DATA_DIR) / 'hackathon-private')

    def url(self, name):
        raise ValueError('Hackathon assets must be downloaded through an authorised view.')


def asset_path(instance, filename):
    return (
        f'{instance.hackathon_id}/{instance.project.team_id}/{instance.project_id}/'
        f'{instance.kind}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'
    )


class StudentAccount(TimestampedModel):
    """Campus login identifier, independent of an event registration."""

    student_id = models.CharField('学号', max_length=32, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='student_account')


class Hackathon(TimestampedModel):
    """Public event registry; children are isolated by the linked Event scope."""

    event = models.OneToOneField('base.Event', on_delete=models.CASCADE, related_name='hackathon')
    slug = models.SlugField(unique=True)
    name = models.CharField('活动名称', max_length=120)
    tagline = models.CharField('一句话介绍', max_length=200, default='把好奇心，变成真实的作品。')
    description = models.TextField('活动介绍', blank=True)
    venue = models.CharField('场地与候场区', max_length=200, blank=True)
    contact = models.CharField('组委会联系方式', max_length=200, blank=True)
    registration_deadline = models.DateTimeField('报名截止')
    team_freeze_at = models.DateTimeField('队伍冻结')
    soft_deadline = models.DateTimeField('建议提交时间')
    submission_deadline = models.DateTimeField('作品提交截止')
    freeze_at = models.DateTimeField('材料最终冻结')
    team_size = models.PositiveSmallIntegerField(
        '每队人数上限', default=5, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    demo_seconds = models.PositiveSmallIntegerField(
        '展示秒数', default=120, validators=[MinValueValidator(30), MaxValueValidator(1800)]
    )
    transition_seconds = models.PositiveSmallIntegerField(
        '换场秒数', default=30, validators=[MinValueValidator(10), MaxValueValidator(300)]
    )
    is_public = models.BooleanField('活动公开', default=False)
    gallery_open = models.BooleanField('公开已审核且同意展示的作品', default=False)
    results_published = models.BooleanField('公布成绩与评语', default=False)
    judging_open = models.BooleanField('开放评分', default=False)
    innovation_weight = models.PositiveSmallIntegerField(
        '创新性权重（%）', default=30, validators=[MaxValueValidator(100)]
    )
    implementation_weight = models.PositiveSmallIntegerField(
        '完成度与实机演示权重（%）', default=40, validators=[MaxValueValidator(100)]
    )
    impact_weight = models.PositiveSmallIntegerField(
        '实际价值权重（%）', default=20, validators=[MaxValueValidator(100)]
    )
    presentation_weight = models.PositiveSmallIntegerField(
        '表达权重（%）', default=10, validators=[MaxValueValidator(100)]
    )
    score_aggregation = models.CharField(
        '评委总分汇总方式',
        max_length=12,
        default='mean',
        choices=[('mean', '全部有效评分取平均（默认）'), ('trimmed', '去掉最高、最低分后取平均')],
    )
    trim_extremes = models.PositiveSmallIntegerField(
        '两端各去掉的评分数量', default=1, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    scoring_revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-event__date_from']

    def __str__(self):
        return self.name

    def clean(self):
        dates = [
            self.registration_deadline,
            self.team_freeze_at,
            self.soft_deadline,
            self.submission_deadline,
            self.freeze_at,
        ]
        if all(dates) and dates != sorted(dates):
            raise ValidationError('请按报名截止、队伍冻结、建议提交、提交截止、材料冻结的顺序设置时间。')

    @property
    def submissions_open(self):
        return timezone.now() < self.submission_deadline

    @property
    def teams_open(self):
        return timezone.now() < self.team_freeze_at


class EventRecord(TimestampedModel):
    hackathon = models.ForeignKey(Hackathon, on_delete=models.CASCADE)
    objects = ScopedManager(event='hackathon__event')

    class Meta:
        abstract = True


class Track(EventRecord):
    name = models.CharField('赛道', max_length=80)
    description = models.TextField('赛道介绍', blank=True)
    icon = models.CharField(max_length=20, default='code')

    def __str__(self):
        return self.name


class Participant(EventRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField('姓名 / 常用名', max_length=80)
    school = models.CharField('学校 / 组织', max_length=120)
    contact = models.CharField('手机 / 微信（仅工作人员可见）', max_length=100)
    skills = models.CharField('技能与兴趣', max_length=200, blank=True)
    keywords = models.JSONField('个人关键词', default=list, blank=True)
    tagline = models.CharField('一句话自我介绍', max_length=120, blank=True)
    bio = models.TextField('自我介绍与想做的项目', max_length=1000, blank=True)
    looking_for_team = models.BooleanField('在组队广场展示我的名字、技能和介绍', default=False)
    availability = models.CharField('无法到场的时段 / 特殊安排（仅工作人员可见）', max_length=250, blank=True)
    consent = models.BooleanField('同意组委会为组织本次活动使用上述信息', default=False)
    checked_in = models.BooleanField(default=False)
    certificate_name = models.CharField('证书姓名', max_length=80, blank=True)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True, related_name='members')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'user'], name='hack_participant_user')]

    @property
    def code(self):
        return f'P-{self.id.hex[:8].upper()}'

    def __str__(self):
        return f'{self.code} · {self.name}'


class Team(EventRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('队伍名称', max_length=80)
    captain = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name='captained_teams')
    track = models.ForeignKey(Track, on_delete=models.PROTECT)
    intro = models.TextField('项目想法 / 招募说明', max_length=1000, blank=True)
    needs = models.CharField('希望队友具备的技能', max_length=200, blank=True)
    recruiting = models.BooleanField('公开招募队友', default=True)
    invite_code = models.CharField(max_length=32, unique=True, default=invitation_code)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'name'], name='hack_team_name')]
        ordering = ['created']

    @property
    def code(self):
        return f'T-{self.id.hex[:8].upper()}'

    def __str__(self):
        return f'{self.code} · {self.name}'


class JoinRequest(EventRecord):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='join_requests')
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE)
    message = models.CharField('申请说明', max_length=300)
    state = models.CharField(
        max_length=12,
        default='pending',
        choices=[('pending', '待处理'), ('accepted', '已加入'), ('declined', '已婉拒')],
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=['team', 'participant'], name='hack_join_request')]


class Project(EventRecord):
    class State(models.TextChoices):
        DRAFT = 'draft', '草稿'
        QUEUED = 'queued', '已登记，等待材料上传'
        SUBMITTED = 'submitted', '待检查'
        CHANGES = 'changes', '需要补充'
        APPROVED = 'approved', '已通过'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.OneToOneField(Team, on_delete=models.CASCADE, related_name='project')
    title = models.CharField('作品名称', max_length=120)
    summary = models.CharField('一句话介绍', max_length=240)
    description = models.TextField('解决什么问题，已经实现什么，如何运行', max_length=10000, blank=True)
    repository_url = models.URLField('代码仓库', blank=True)
    demo_url = models.URLField('可访问的 Demo', blank=True)
    backup_url = models.URLField('备用演示 / 录屏链接', blank=True)
    demo_instructions = models.TextField('实机演示步骤 / 设备需求', max_length=2000, blank=True)
    checklist = models.BooleanField('已完成提交自查与演示测试', default=False)
    public_consent = models.BooleanField('同意在作品展厅与历届归档公开介绍和链接', default=False)
    state = models.CharField(max_length=12, choices=State.choices, default=State.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submission_intent_at = models.DateTimeField(null=True, blank=True)
    submission_client_clicked_at = models.DateTimeField(null=True, blank=True)
    submission_receipt = models.UUIDField(null=True, blank=True)
    submission_manifest = models.JSONField(default=dict, blank=True)
    submission_manifest_hash = models.CharField(max_length=64, blank=True)
    revision = models.PositiveIntegerField(default=0)
    review_note = models.TextField('检查意见 / 补录理由', blank=True)
    equipment_checked = models.BooleanField('投影、声音、视频切换已演练', default=False)
    award = models.CharField('奖项 / 后续孵化资源', max_length=200, blank=True)

    @property
    def code(self):
        return f'W-{self.id.hex[:8].upper()}'

    def __str__(self):
        return self.title


class Asset(EventRecord):
    class Kind(models.TextChoices):
        POSTER = 'poster', '项目海报'
        SLIDES = 'slides', '演示文稿'
        VIDEO = 'video', '备用录屏'
        SOURCE = 'source', '项目附件'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    kind = models.CharField('材料类型', max_length=12, choices=Kind.choices)
    file = models.FileField(storage=PrivateStorage(), upload_to=asset_path, max_length=512)
    filename = models.CharField(max_length=255)
    original_filename = models.CharField('原文件名', max_length=255, blank=True)
    checksum = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'kind'], name='hack_asset_kind')]


class UploadSession(EventRecord):
    class State(models.TextChoices):
        UPLOADING = 'uploading', '上传中'
        COMPLETE = 'complete', '已完成'
        CANCELLED = 'cancelled', '已取消'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='uploads')
    kind = models.CharField(max_length=12, choices=Asset.Kind.choices)
    original_filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    offset = models.PositiveBigIntegerField(default=0)
    state = models.CharField(max_length=12, choices=State.choices, default=State.UPLOADING)
    expires_at = models.DateTimeField(db_index=True)
    checksum = models.CharField(max_length=64, blank=True)
    expected_checksum = models.CharField(max_length=64)
    client_clicked_at = models.DateTimeField(null=True, blank=True)
    checksum_bound_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.CharField(max_length=500, blank=True)
    asset = models.ForeignKey(Asset, on_delete=models.SET_NULL, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'kind'], condition=models.Q(state='uploading'), name='hack_active_upload_kind'
            ),
            models.CheckConstraint(condition=models.Q(offset__lte=models.F('size')), name='hack_upload_offset'),
        ]


class Resource(EventRecord):
    class Category(models.TextChoices):
        GUIDE = 'guide', '参赛指南'
        LEARN = 'learn', '开发教程'
        DEMO = 'demo', '提交与路演'
        VOLUNTEER = 'volunteer', '志愿者手册'
        INCUBATE = 'incubate', '赛后成长'

    title = models.CharField('标题', max_length=160)
    category = models.CharField('分类', max_length=12, choices=Category.choices)
    summary = models.CharField('摘要', max_length=250)
    body = models.TextField('正文（纯文本，按段落阅读）')
    url = models.URLField('延伸链接', blank=True)
    minutes = models.PositiveSmallIntegerField('预计阅读分钟', default=5)
    required = models.BooleanField('标记为必读', default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['position', 'pk']


class ResourceRead(EventRecord):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['resource', 'user'], name='hack_resource_read')]


class Announcement(EventRecord):
    title = models.CharField('通知标题', max_length=160)
    body = models.TextField('通知内容')
    pinned = models.BooleanField('置顶', default=False)
    audience = models.CharField(
        '接收对象', max_length=12, default='all', choices=[('all', '所有人'), ('staff', '工作人员')]
    )

    class Meta:
        ordering = ['-pinned', '-created']


class ScheduleItem(EventRecord):
    title = models.CharField('环节', max_length=120)
    starts_at = models.DateTimeField('开始时间')
    ends_at = models.DateTimeField('结束时间')
    location = models.CharField('地点', max_length=120)
    description = models.TextField('说明', blank=True)
    kind = models.CharField('类型', max_length=30, default='活动')

    class Meta:
        ordering = ['starts_at']

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError('结束时间必须晚于开始时间。')


class StaffAssignment(EventRecord):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField('角色', max_length=12, choices=[('judge', '评委'), ('volunteer', '志愿者')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hackathon', 'user', 'role'], name='hack_staff_role')]


class DemoSlot(EventRecord):
    class State(models.TextChoices):
        WAITING = 'waiting', '待候场'
        READY = 'ready', '准备就绪'
        LIVE = 'live', '正在展示'
        DONE = 'done', '已完成'
        DEFERRED = 'deferred', '已顺延'

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='slot')
    position = models.PositiveIntegerField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.WAITING)
    started_at = models.DateTimeField(null=True, blank=True)
    paused_remaining = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField('时段调整 / 特殊安排', max_length=250, blank=True)

    class Meta:
        ordering = ['position', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['hackathon'], condition=models.Q(state='live'), name='hack_one_live_slot')
        ]


class Score(EventRecord):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='scores')
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    innovation = models.PositiveSmallIntegerField('创新性 · 30%', validators=[MaxValueValidator(10)])
    implementation = models.PositiveSmallIntegerField('完成度与实机演示 · 40%', validators=[MaxValueValidator(10)])
    impact = models.PositiveSmallIntegerField('实际价值 · 20%', validators=[MaxValueValidator(10)])
    presentation = models.PositiveSmallIntegerField('表达 · 10%', validators=[MaxValueValidator(10)])
    breakthrough = models.BooleanField('提名最佳突破（每位评委最多 3 项）', default=False)
    creativity = models.BooleanField('提名最佳创意（每位评委最多 3 项）', default=False)
    feedback = models.TextField('给团队的改进建议（公布成绩后可见）', max_length=3000, blank=True)
    final = models.BooleanField('确认评分', default=False)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['project', 'judge'], name='hack_score_judge'),
            models.CheckConstraint(
                condition=models.Q(innovation__lte=10, implementation__lte=10, impact__lte=10, presentation__lte=10),
                name='hack_score_range',
            ),
        ]

    @property
    def total(self):
        return weighted_total(self, self.hackathon)


class VolunteerTask(EventRecord):
    title = models.CharField('任务', max_length=150)
    description = models.TextField('操作步骤 / 交接说明')
    location = models.CharField('岗位地点', max_length=120)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    backup = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True
    )
    due_at = models.DateTimeField('到岗 / 截止时间')
    state = models.CharField(
        max_length=12, default='todo', choices=[('todo', '待认领'), ('doing', '进行中'), ('done', '已完成')]
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    approved_minutes = models.PositiveIntegerField('核准志愿分钟', default=0)

    class Meta:
        ordering = ['due_at']


class SupportTicket(EventRecord):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    subject = models.CharField('问题标题', max_length=150)
    body = models.TextField('问题描述 / 错误出现的时间', max_length=5000)
    category = models.CharField(
        '类别',
        max_length=20,
        choices=[('tech', '技术支持'), ('submission', '提交异常'), ('schedule', '路演时段调整'), ('other', '其他')],
    )
    state = models.CharField(
        '状态', max_length=12, default='open', choices=[('open', '待处理'), ('doing', '处理中'), ('resolved', '已解决')]
    )
    reply = models.TextField('处理说明', blank=True)
    handler = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['-created']


class AuditEntry(EventRecord):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=100, blank=True)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ['-created']


class StationToken(EventRecord):
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
