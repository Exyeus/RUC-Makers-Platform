from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event, Organizer
from eventyay.hackathon.models import Announcement, Hackathon, Resource, ScheduleItem, Track
from eventyay.hackathon.seed import RESOURCES


class Command(BaseCommand):
    help = 'Create a hackathon workspace on an Eventyay event, with editable guides and a provisional schedule.'

    def add_arguments(self, parser):
        parser.add_argument('--organizer', default='hackathon')
        parser.add_argument('--event', default='next-hackathon')
        parser.add_argument('--slug', default='next')
        parser.add_argument('--name', default='下一届黑客松')
        parser.add_argument(
            '--start-date', help='First day in YYYY-MM-DD, Asia/Shanghai; omitted dates are provisional.'
        )
        parser.add_argument('--publish', action='store_true', help='Make the workspace visible to participants.')

    @transaction.atomic
    def handle(self, organizer, event, slug, name, start_date, publish, **options):
        if Hackathon.objects.filter(slug=slug).exists():
            self.stdout.write(f'Workspace /hackathon/{slug}/ already exists; no data was overwritten.')
            return
        try:
            first_day = date.fromisoformat(start_date) if start_date else timezone.localdate() + timedelta(days=30)
        except ValueError as error:
            raise CommandError('Use --start-date YYYY-MM-DD.') from error
        zone = ZoneInfo('Asia/Shanghai')
        start = datetime.combine(first_day, time(18), tzinfo=zone)
        end = datetime.combine(first_day + timedelta(days=2), time(17, 30), tzinfo=zone)
        org, _ = Organizer.objects.get_or_create(slug=organizer, defaults={'name': '黑客松组委会'})
        ev, _ = Event.objects.get_or_create(
            organizer=org,
            slug=event,
            defaults={
                'name': name,
                'date_from': start,
                'date_to': end,
                'currency': 'CNY',
                'locale': 'zh-hans',
                'timezone': 'Asia/Shanghai',
                'is_public': False,
                'live': False,
            },
        )
        if Hackathon.objects.filter(event=ev).exists():
            raise CommandError('This Eventyay event already has a hackathon workspace; choose its existing slug.')
        with scope(event=ev):
            h = Hackathon.objects.create(
                event=ev,
                slug=slug,
                name=name,
                tagline='把好奇心，变成真实的作品。',
                description='三天两晚，和一群有趣的人，从一个「如果」开始，做出一个真实的答案。无论是第一次写代码，还是准备好挑战自己，这里都有你的位置。',
                venue='主会场 / 候场区 · 待确认',
                registration_deadline=start - timedelta(days=1),
                team_freeze_at=start + timedelta(hours=14),
                soft_deadline=end - timedelta(hours=10),
                submission_deadline=end - timedelta(hours=7),
                freeze_at=end - timedelta(hours=5),
                is_public=publish,
            )
            Track.objects.bulk_create(
                [
                    Track(
                        hackathon=h,
                        name='AI 与日常生活',
                        description='让技术走进真实场景，用一个小工具，改变一点点日常。',
                    ),
                    Track(hackathon=h, name='开放创新', description='打破题目的边界，把你一直想做的事情，真正做出来。'),
                ]
            )
            for position, resource in enumerate(RESOURCES):
                Resource.objects.create(hackathon=h, position=position, **resource)
            Announcement.objects.create(
                hackathon=h,
                title='欢迎来到下一届黑客松工作台',
                pinned=True,
                body='所有报名、组队、教程、材料提交和现场安排都会在这里集中更新。'
                '请先完成参赛信息，登记特殊时段，再认识你的队友。'
                '本活动当前为筹备状态，页面中的日期、地点、赛道与规则需由组委会在活动设置和内容管理中确认后正式发布。',
            )
            Announcement.objects.create(
                hackathon=h,
                title='先读指南，给最后一天留一点从容',
                body='学习中心已提供 Git、AI 辅助开发、电子海报、实机演示与志愿者指南。'
                '建议提前保存一次作品草稿并测试附件上传，路演前在现场设备完整演练。',
            )
            program = [
                (0, 18, 0, 60, '签到与开场', '见面', '认识彼此，介绍赛道、规则、支持入口和岗位负责人。'),
                (0, 19, 0, 60, '破冰与组队', '连接', '两人互相介绍一个想解决的问题，按兴趣找到互补的队友。'),
                (1, 9, 0, 60, '开发起步与 Git 工作坊', '学习', '跑通第一条协作流程，在支持中心登记遇到的问题。'),
                (1, 14, 0, 60, '中期交流与技术答疑', '创造', '展示一个已经能运行的小功能，交换建议。'),
                (1, 16, 0, 30, '休息一下 · 茶歇交流', '休息', '离开屏幕，认识其他赛道的伙伴。'),
                (2, 9, 0, 60, '材料自查与提交', '提交', '确认 Demo、仓库、电子海报与备用录屏，完成正式提交。'),
                (2, 11, 0, 60, '投影与演示彩排', '演练', '依次检查文稿、视频切换、声音、翻页笔和转接线。'),
                (2, 14, 0, 120, 'Demo Day · 让作品说话', '路演', '以路演现场页面为准候场，提前三队准备。'),
                (2, 16, 30, 60, '评审分享与颁奖', '庆祝', '公布结果、合影，介绍赛后社区共建与孵化衔接。'),
            ]
            for day, hour, minute, duration, title, kind, description in program:
                starts_at = datetime.combine(first_day + timedelta(days=day), time(hour, minute), tzinfo=zone)
                ScheduleItem.objects.create(
                    hackathon=h,
                    title=title,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=duration),
                    location='场地待确认',
                    description=description,
                    kind=kind,
                )
        self.stdout.write(self.style.SUCCESS(f'Created /hackathon/{slug}/ — edit dates and venue before announcing.'))
