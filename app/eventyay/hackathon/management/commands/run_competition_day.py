"""Run due competition-day steps for every hackathon; schedule every minute from cron."""

from django.core.management.base import BaseCommand
from django_scopes import scope

from eventyay.hackathon.competition_day import lock_and_start
from eventyay.hackathon.models import Hackathon


class Command(BaseCommand):
    help = '执行已到时间的比赛日步骤（提交截止锁定并开放初评）。可重复运行。'

    def handle(self, *args, **options):
        for hackathon in Hackathon.objects.select_related('event'):
            with scope(event=hackathon.event):
                result = lock_and_start(hackathon)
            self.stdout.write(f'{hackathon.slug}: {result}')
