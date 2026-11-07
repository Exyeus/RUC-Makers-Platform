from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django_scopes import scope

from eventyay.hackathon.models import Asset, Hackathon, UploadSession
from eventyay.hackathon.storage import PrivateStorage, temporary_root
from eventyay.hackathon.uploads import expire_uploads, remove_file


class Command(BaseCommand):
    help = 'Expire idle uploads and collect unreferenced private files older than 48 hours.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, dry_run=False, **options):
        cutoff = timezone.now() - timedelta(hours=48)
        live_parts, referenced = set(), set()
        for hackathon in Hackathon.objects.select_related('event'):
            with scope(event=hackathon.event):
                if not dry_run:
                    expire_uploads(hackathon)
                sessions = UploadSession.objects.filter(
                    hackathon=hackathon, state='uploading', expires_at__gt=timezone.now()
                )
                live_parts.update(f'{pk}.part' for pk in sessions.values_list('pk', flat=True))
                referenced.update(Asset.objects.filter(hackathon=hackathon).values_list('file', flat=True))
        removed = 0
        storage = PrivateStorage()
        for name, modified in storage.iter_objects():
            if modified >= cutoff or name in referenced:
                continue
            # Recheck at deletion time. New objects use UUID names and are age gated.
            event_id = name.split('/', 1)[0]
            if not event_id.isdecimal():
                continue
            hackathon = Hackathon.objects.filter(pk=event_id).select_related('event').first()
            if hackathon:
                with scope(event=hackathon.event):
                    if Asset.objects.filter(file=name).exists():
                        continue
            self.stdout.write(f'Unreferenced: {name}')
            if not dry_run:
                storage.delete(name)
            removed += 1
        for path in temporary_root().glob('*.part'):
            if not path.is_symlink() and path.name not in live_parts and path.stat().st_mtime < cutoff.timestamp():
                self.stdout.write(f'Temporary: {path.name}')
                if not dry_run:
                    remove_file(path)
                removed += 1
        self.stdout.write(f'{"Would remove" if dry_run else "Removed"}: {removed} files')
