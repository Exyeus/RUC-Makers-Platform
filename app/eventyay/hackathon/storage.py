"""Private final assets; resumable parts deliberately remain on one ECS disk."""
import io
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property


try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None

STORAGE_ERRORS = (OSError, BotoCoreError, ClientError) if boto3 else (OSError,)


logger = logging.getLogger(__name__)


def local_root():
    return Path(settings.DATA_DIR) / 'hackathon-private'


def temporary_root():
    return local_root() / '.uploads'


class DurableFileStorage(FileSystemStorage):
    def _save(self, name, content):
        saved = super()._save(name, content)
        with open(self.path(saved), 'rb') as source:
            os.fsync(source.fileno())
        # Include newly created parent directories in the durability boundary.
        parent = Path(self.path(saved)).parent
        root = Path(self.location)
        while parent.is_relative_to(root):
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            parent = parent.parent
        return saved


class RangeReader(io.RawIOBase):
    """Seek by opening an S3 Range request, without buffering a whole video."""
    def __init__(self, storage, name):
        self.storage, self.name = storage, name
        self.length = storage.size(name)
        self.position = 0
        self.body = None

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else (self.position if whence == 1 else self.length) + offset
        if position < 0:
            raise ValueError('Negative seek')
        if self.body and 0 < position - self.position <= 256 * 1024:
            self.read(position - self.position)
        if position != self.position and self.body:
            self.body.close()
            self.body = None
        self.position = position
        return position

    def read(self, size=-1):
        if self.closed:
            raise ValueError('Read on closed asset')
        if self.position >= self.length or size == 0:
            return b''
        if self.body is None:
            self.body = self.storage.client.get_object(
                Bucket=self.storage.bucket, Key=self.storage.key(self.name), Range=f'bytes={self.position}-'
            )['Body']
        data = self.body.read(size if size >= 0 else None)
        self.position += len(data)
        return data

    def close(self):
        if self.body:
            self.body.close()
        super().close()


class S3Storage(Storage):
    def __init__(self):
        if boto3 is None:
            raise ImproperlyConfigured('Install deployment/requirements-s3.txt to enable S3 storage.')
        conf = settings.HACKATHON_S3
        self.bucket = conf['bucket']
        self.prefix = conf['prefix'].strip('/')
        if not self.bucket or not self.prefix:
            raise ImproperlyConfigured('S3 bucket and a dedicated nonempty prefix are required.')
        self.client = boto3.client(
            's3', endpoint_url=conf['endpoint'] or None, region_name=conf['region'],
            aws_access_key_id=conf['access_key'] or None, aws_secret_access_key=conf['secret_key'] or None,
            config=Config(signature_version='s3v4', connect_timeout=10, read_timeout=60,
                          retries={'max_attempts': 3, 'mode': 'standard'},
                          request_checksum_calculation='when_required', response_checksum_validation='when_required',
                          s3={'addressing_style': conf['addressing_style']}),
        )

    def key(self, name):
        if not name or name.startswith('/') or '\\' in name or '..' in name.split('/'):
            raise SuspiciousFileOperation('Invalid private asset name')
        return f'{self.prefix}/{name}'

    def _open(self, name, mode='rb'):
        if mode != 'rb':
            raise ValueError('Private objects are read only after upload.')
        return File(RangeReader(self, name), name=name)

    def _save(self, name, content):
        content.seek(0)
        self.client.put_object(Bucket=self.bucket, Key=self.key(name), Body=content)
        return name

    def save_exact(self, name, content):
        if self.exists(name):
            raise FileExistsError(name)
        return self._save(name, content)

    def get_available_name(self, name, max_length=None):
        # Immutable UUID keys prevent retries/rollbacks from overwriting an active asset.
        path = Path(name)
        name = str(path.with_name(f'{uuid4().hex}{path.suffix}'))
        return super().get_available_name(name, max_length)

    def head(self, name):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.key(name))
        except ClientError as error:
            if error.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                raise FileNotFoundError(name) from error
            raise

    def exists(self, name):
        try:
            self.head(name)
        except FileNotFoundError:
            return False
        return True

    def size(self, name):
        return self.head(name)['ContentLength']

    def delete(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self.key(name))

    def iter_objects(self):
        prefix = self.prefix + '/'
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                yield obj['Key'][len(prefix):], obj['LastModified']


@deconstructible(path='eventyay.hackathon.models.PrivateStorage')
class PrivateStorage(Storage):
    @cached_property
    def backend(self):
        backend = settings.HACKATHON_STORAGE_BACKEND
        if backend == 'local':
            return DurableFileStorage(location=local_root())
        if backend == 's3':
            return S3Storage()
        raise ImproperlyConfigured('HACKATHON_STORAGE_BACKEND must be local or s3')

    def _open(self, name, mode='rb'):
        return self.backend.open(name, mode)

    def _save(self, name, content):
        return self.backend.save(name, content)

    def exists(self, name):
        return self.backend.exists(name)

    def size(self, name):
        return self.backend.size(name)

    def path(self, name):
        return self.backend.path(name)

    @property
    def location(self):
        return self.backend.location

    def delete(self, name):
        return self.backend.delete(name)

    def url(self, name):
        raise ValueError('Hackathon assets must be downloaded through an authorised view.')

    def iter_objects(self):
        if isinstance(self.backend, S3Storage):
            yield from self.backend.iter_objects()
        else:
            for path in local_root().rglob('*'):
                if path.is_file() and not path.is_symlink() and '.uploads' not in path.parts:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                    yield path.relative_to(local_root()).as_posix(), modified


def remove_asset(name):
    try:
        PrivateStorage().delete(name)
    except STORAGE_ERRORS:
        # A committed replacement must not become an HTTP failure because GC is unavailable.
        logger.exception('Could not remove unreferenced private asset %s', name)
