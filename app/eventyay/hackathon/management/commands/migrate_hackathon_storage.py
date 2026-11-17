"""Copy referenced immutable assets; verify both ends without changing the database."""
import hashlib

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django_scopes import scope

from eventyay.hackathon.models import Asset, Hackathon
from eventyay.hackathon.storage import DurableFileStorage, S3Storage, local_root


def fingerprint(storage, name):
    digest, size = hashlib.sha256(), 0
    with storage.open(name, 'rb') as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


class Command(BaseCommand):
    help = 'Offline copy/verify private assets. Stop ALL writers and cleanup before running.'

    def add_arguments(self, parser):
        parser.add_argument('--source', choices=('local', 's3'), required=True)
        parser.add_argument('--target', choices=('local', 's3'), required=True)
        parser.add_argument('--copy', action='store_true', help='Copy missing files; default only verifies.')

    def handle(self, source, target, copy=False, **options):
        if source == target:
            raise CommandError('Source and target must differ.')
        backends = {'local': DurableFileStorage(location=local_root()), 's3': S3Storage()}
        src, dst = backends[source], backends[target]
        count = 0
        for hackathon in Hackathon.objects.select_related('event'):
            with scope(event=hackathon.event):
                for asset in Asset.objects.filter(hackathon=hackathon).iterator():
                    expected = (asset.size, asset.checksum)
                    name = asset.file.name
                    if fingerprint(src, name) != expected:
                        raise CommandError(f'Source integrity mismatch: asset {asset.pk}')
                    if not dst.exists(name):
                        if not copy:
                            raise CommandError(f'Target missing: asset {asset.pk}; use --copy with writers stopped.')
                        with src.open(name, 'rb') as stream:
                            # Preserve names for the configuration switch. Never overwrite existing objects.
                            if target == 's3':
                                dst.save_exact(name, File(stream))
                            elif dst.save(name, File(stream)) != name:
                                raise CommandError('Target name collision; keep writers stopped.')
                    if fingerprint(dst, name) != expected:
                        raise CommandError(f'Target integrity mismatch: asset {asset.pk}')
                    count += 1
        self.stdout.write(f'Verified {count} referenced assets; database and source unchanged.')
