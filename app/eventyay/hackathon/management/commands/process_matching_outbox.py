from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_scopes import scope

from eventyay.hackathon.matching import consume_outbox
from eventyay.hackathon.models import Hackathon, MatchIntent, TeamProposal
from eventyay.hackathon.services import lock_event


class Command(BaseCommand):
    help = 'Expire matching requests and consume durable site receipts; no automatic point awards.'

    def add_arguments(self, parser):
        parser.add_argument('--event', dest='event_slug', required=True, help='Hackathon slug')

    def handle(self, event_slug, **options):
        h = Hackathon.objects.select_related('event').get(slug=event_slug)
        with scope(event=h.event), transaction.atomic():
            h = lock_event(h)
            now = timezone.now()
            intents = MatchIntent.objects.filter(hackathon=h, state='pending', expires_at__lte=now).update(
                state='expired'
            )
            proposals = TeamProposal.objects.filter(hackathon=h, state='awaiting_consent', expires_at__lte=now).update(
                state='expired'
            )
            delivered = consume_outbox(h)
        self.stdout.write(f'Expired intents: {intents}; proposals: {proposals}; delivered receipts: {delivered}')
