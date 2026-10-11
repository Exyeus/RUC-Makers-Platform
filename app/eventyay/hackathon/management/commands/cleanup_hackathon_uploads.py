from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone
from django_scopes import scope

from eventyay.hackathon.models import Asset, Hackathon, PrivateStorage, UploadSession
from eventyay.hackathon.uploads import expire_uploads, remove_file


class Command(BaseCommand):
    help = 'Expire idle uploads and remove unreferenced upload files older than 48 hours.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only list candidate files; do not change data.')

    def handle(self, dry_run=False, **options):
        root = Path(PrivateStorage().location)
        cutoff = timezone.now() - timedelta(hours=48)
        live_parts = set()
        removed = 0
        for hackathon in Hackathon.objects.select_related('event'):
            with scope(event=hackathon.event):
                if not dry_run:
                    expire_uploads(hackathon)
                sessions = UploadSession.objects.filter(
                    hackathon=hackathon, state='uploading', expires_at__gt=timezone.now()
                )
                live_ids = {pk.hex for pk in sessions.values_list('pk', flat=True)}
                live_parts.update(f'{pk}.part' for pk in sessions.values_list('pk', flat=True))
                referenced = set(Asset.objects.filter(hackathon=hackathon).values_list('file', flat=True))
                for path in (root / str(hackathon.pk)).rglob('*'):
                    if (
                        not path.is_file()
                        or path.is_symlink()
                        or path.stat().st_mtime >= cutoff.timestamp()
                        or path.relative_to(root).as_posix() in referenced
                        or path.stem in live_ids
                    ):
                        continue
                    self.stdout.write(f'Unreferenced: {path}')
                    if not dry_run:
                        remove_file(path)
                    removed += 1
                # Keep upload receipts for support and deadline disputes for the event's lifetime.
        for path in (root / '.uploads').glob('*.part'):
            if path.name not in live_parts and path.stat().st_mtime < cutoff.timestamp():
                self.stdout.write(f'Temporary: {path}')
                if not dry_run:
                    remove_file(path)
                removed += 1
        self.stdout.write(f'{"Would remove" if dry_run else "Removed"}: {removed} files')
