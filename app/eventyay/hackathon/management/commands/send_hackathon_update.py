"""Preview or explicitly deliver opt-in activity mail, including non-admitted accounts."""

import smtplib
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from eventyay.hackathon import identity
from eventyay.hackathon.account_models import AccountSecurity, UpdateDelivery
from eventyay.hackathon.models import Hackathon


class Command(BaseCommand):
    help = (
        'Preview opted-in verified recipients; --send explicitly delivers individual messages with unsubscribe links.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--hackathon', required=True)
        parser.add_argument('--audience', choices=['activity', 'future'], required=True)
        parser.add_argument(
            '--campaign', required=True, help='Stable campaign ID used to skip previously delivered mail.'
        )
        parser.add_argument('--subject', required=True)
        parser.add_argument('--body-file', required=True)
        parser.add_argument('--send', action='store_true', help='Actually send; omitted means count-only preview.')

    def handle(self, hackathon, audience, campaign, subject, body_file, send, **options):
        if len(campaign) > 100 or '\n' in subject or '\r' in subject:
            raise CommandError('Invalid campaign or subject.')
        try:
            event = Hackathon.objects.get(slug=hackathon)
            body = Path(body_file).read_text(encoding='utf-8')
        except (Hackathon.DoesNotExist, OSError, UnicodeError) as error:
            raise CommandError('Activity or UTF-8 body file unavailable.') from error
        preference = 'activity_updates' if audience == 'activity' else 'future_updates'
        profiles = (
            AccountSecurity.objects.filter(
                **{preference: True},
                verified_at__isnull=False,
                verified_email__iexact=F('user__email'),
                user__is_active=True,
                user__is_spam=False,
            )
            .exclude(verified_email='')
            .select_related('user')
        )
        delivered = UpdateDelivery.objects.filter(hackathon=event, campaign=campaign, sent_at__isnull=False)
        profiles = profiles.exclude(user_id__in=delivered.values('user_id'))
        self.stdout.write(f'Eligible unsent recipients: {profiles.count()}; delivery={send}')
        if not send:
            return
        sent = failed = 0
        with get_connection() as connection:
            for initial in profiles.iterator(chunk_size=100):
                with transaction.atomic():
                    profile = AccountSecurity.objects.select_for_update().select_related('user').get(pk=initial.pk)
                    if not getattr(profile, preference) or not identity.email_verified(profile.user):
                        continue
                    delivery, _ = UpdateDelivery.objects.select_for_update().get_or_create(
                        hackathon=event,
                        campaign=campaign,
                        user=profile.user,
                    )
                    if delivery.sent_at:
                        continue
                    path = reverse('hackathon:unsubscribe-account')
                    link = identity.absolute_url(path) + '?token=' + identity.unsubscribe_token(profile)
                    message = EmailMessage(
                        subject=subject,
                        body=body + '\n\n取消邮件订阅：' + link,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[profile.user.email],
                        connection=connection,
                        headers={'List-Unsubscribe': f'<{link}>'},
                    )
                    try:
                        result = message.send(fail_silently=False)
                    except (smtplib.SMTPException, OSError):
                        failed += 1
                        continue
                    if result:
                        delivery.sent_at = timezone.now()
                        delivery.save(update_fields=['sent_at', 'updated'])
                        sent += 1
                    else:
                        failed += 1
        self.stdout.write(f'Sent: {sent}; failed: {failed}. No recipient addresses printed.')
        if failed:
            raise CommandError('Some messages failed; retry with the same campaign ID to skip recorded deliveries.')
