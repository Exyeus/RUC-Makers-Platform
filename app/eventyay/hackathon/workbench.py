"""Presentation choices for the participant overview; never submission gates."""

from dataclasses import dataclass
from datetime import datetime

from django.urls import reverse

from eventyay.hackathon.models import Hackathon, Participant, Project, Resource, SupportTicket


@dataclass(frozen=True)
class Task:
    key: str
    title: str
    detail: str
    label: str
    url: str
    route: str


def next_task(
    hackathon: Hackathon,
    participant: Participant | None,
    project: Project | None,
    unread: Resource | None,
    now: datetime,
    status: dict,
    waiting_ticket: SupportTicket | None = None,
) -> Task:
    """Pick the one action the participant should take next.

    ``status`` is :func:`eventyay.hackathon.services.project_status`; the workbench
    and the project page must describe the same current version with the same words.
    ``waiting_ticket`` is the organizer's open question to this person, if any; it
    never outranks registration, teaming, the deadline or material fixes, and the
    home page keeps it visible on its own.
    """

    def task(key, title, detail, label, route, **kwargs):
        return Task(
            key,
            title,
            detail,
            label,
            reverse(f'hackathon:{route}', kwargs={'hackathon_slug': hackathon.slug, **kwargs}),
            route,
        )

    if hackathon.results_published:
        return task(
            'results', '查看活动结果', '成绩与评语已公布，作品与提交记录继续保留。', '查看结果与作品', 'results'
        )
    if now >= hackathon.submission_deadline:
        if status['key'] == 'queued':
            return task(
                'queued',
                '核对排队提交状态',
                '截止前登记的上传仍需核对材料与回执；遇到异常请联系支持。',
                '查看上传与回执',
                'project',
            )
        if project and project.submitted_at:
            return task(
                'closed',
                '查看提交记录',
                '作品提交已截止。保留回执，关注后续评审与路演安排。',
                '查看提交回执',
                'project',
            )
        return task(
            'closed',
            '作品提交已截止',
            '如提交时遇到异常，请向支持团队说明发生时间与相关证据。',
            '联系支持团队',
            'support',
        )
    if not participant:
        if now >= hackathon.registration_deadline:
            return task(
                'registration-closed', '报名已截止', '如需咨询参赛资格，请联系支持团队。', '咨询参赛资格', 'support'
            )
        return task(
            'register',
            '完善参赛信息',
            '留下你的技能、联系方式与可参与时段，让接下来的协作更顺畅。',
            '填写参赛信息',
            'register',
        )
    if not participant.team_id:
        if not hackathon.teams_open:
            return task(
                'team-closed', '队伍名单已冻结', '如需处理组队异常，请联系支持团队。', '咨询组队安排', 'support'
            )
        return task(
            'team', '找到你的队友', '去组队广场认识伙伴，或按活动规则创建自己的队伍。', '寻找或创建队伍', 'teams'
        )
    if status['key'] == 'queued':
        return task(
            'queued',
            '等待材料上传完成',
            '提交意图已登记；材料全部上传并通过校验后才会正式提交。',
            '查看上传与回执',
            'project',
        )
    if status['key'] == 'upload_stalled':
        return task(
            'upload_stalled',
            '排队提交已中止',
            status['detail'],
            '重新上传并提交',
            'project',
        )
    if status['key'] == 'resubmit':
        return task(
            'resubmit',
            '把最新修改正式提交',
            status['detail'],
            '核对缺项并再次提交',
            'project',
        )
    if status['key'] in {'review', 'changes'}:
        return task(
            status['key'],
            '检查并补充作品材料',
            status['detail'],
            '查看检查意见',
            'project',
        )
    # 工单只顶掉没有时限的动作（已提交、阅读推荐）；报名、组队、截止与材料补充都排在它前面，
    # 等本人回复的工单另在首页帮助面板持续可见。
    if waiting_ticket:
        return task(
            'ticket_reply',
            f'工单 #{waiting_ticket.pk} 等你补充',
            '组委会在这条工单里把问题交回给你，回复后会重新回到主办待处理。',
            '查看并回复工单',
            'support',
        )
    if status['done']:
        return task(
            'submitted',
            '作品已提交，核对回执',
            '请核对作品记录与提交回执。如需修改，请查看作品页当前状态。',
            '查看提交回执',
            'project',
        )
    if unread:
        return task(
            'read',
            '阅读参赛必读',
            '了解提交要求、评审规则与路演安排。阅读推荐不会限制作品入口。',
            '阅读参赛必读',
            'resource',
            resource_id=unread.pk,
        )
    return task(
        'project',
        '准备并提交作品',
        '整理 Demo、代码仓库、电子海报与备用录屏，由队长完成提交。',
        '准备作品材料',
        'project',
    )
