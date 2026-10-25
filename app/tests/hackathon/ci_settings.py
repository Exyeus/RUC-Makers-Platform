"""Isolated activity-test settings; never use for serving an event."""

import atexit
import os
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


os.environ['EVY_RUNNING_ENVIRONMENT'] = 'testing'
os.environ['EVY_DOCS_BUILD'] = '1'

from tests.tickets.settings import *  # noqa: E402,F403


if not os.environ.get('CI_POSTGRES_HOST'):
    raise ImproperlyConfigured('Set CI_POSTGRES_HOST to an isolated PostgreSQL service before running activity CI.')

CI_TEMP = tempfile.TemporaryDirectory(prefix='eventyay-activity-ci-')
atexit.register(CI_TEMP.cleanup)
DATA_DIR = CI_TEMP.name
MEDIA_ROOT = str(Path(DATA_DIR) / 'media')
LOG_DIR = str(Path(DATA_DIR) / 'logs')
SECRET_KEY = 'activity-ci-only-not-a-deployment-secret'
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'eventyay_ci',
        'TEST': {'NAME': 'test_eventyay_ci'},
        'HOST': os.environ['CI_POSTGRES_HOST'],
        'PORT': os.environ.get('CI_POSTGRES_PORT', '5432'),
        'USER': os.environ.get('CI_POSTGRES_USER', 'eventyay'),
        'PASSWORD': os.environ.get('CI_POSTGRES_PASSWORD', ''),
        'CONN_MAX_AGE': 0,
    }
}
# Always exercise the PostgreSQL migrations, including integrity triggers.
MIGRATION_MODULES = {}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {
    name: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'activity-ci-' + name}
    for name in ('default', 'process', 'redis', 'testcache')
}
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
HAS_REDIS = False
REDIS_USE_PUBSUB = False
HAS_CELERY = False
CELERY_BROKER_URL = None
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = True
