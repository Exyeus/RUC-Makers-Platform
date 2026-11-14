"""Local private evidence, independent of public media and expiring OSS tokens."""

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.utils.deconstruct import deconstructible

from eventyay.hackathon.storage import DurableFileStorage


@deconstructible
class StageEvidenceStorage(DurableFileStorage):
    @property
    def base_location(self):
        return str(Path(settings.DATA_DIR) / 'hackathon-private' / 'stage-evidence')

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def _save(self, name, content):
        # Exclusive creation makes cleanup ownership precise, including failures
        # after a write but before fsync returns. Never overwrite an old proof.
        target = Path(self.path(name))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        created_inode = None
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_inode = os.fstat(descriptor).st_ino
            with os.fdopen(descriptor, 'wb') as output:
                for chunk in content.chunks():
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            parent, root = target.parent, Path(self.location)
            while parent.is_relative_to(root):
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                parent = parent.parent
            return str(target.relative_to(root)).replace(os.sep, '/')
        except BaseException:
            if created_inode is not None:
                try:
                    if target.stat().st_ino == created_inode:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise

    def url(self, name):
        raise ValueError('Evidence is available only through the authorized download view.')


def evidence_path(instance, filename):
    return f'{instance.hackathon_id}/{instance.participant_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'
