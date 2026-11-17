import os
from pathlib import Path
from tests.hackathon.ci_settings import *

name = os.environ['QA_DATABASE']
if not name.startswith('ruc_quality_') or os.environ['CI_POSTGRES_HOST'] != '127.0.0.1':
    raise RuntimeError('QA requires a disposable local database')
DATABASES['default']['NAME'] = name
DEBUG = True
DATA_DIR = os.environ['QA_OUTPUT']
MEDIA_ROOT = str(Path(DATA_DIR) / 'media')
STATIC_ROOT = Path(DATA_DIR) / 'static'
LOG_DIR = str(Path(DATA_DIR) / 'logs')
SITE_URL = 'http://127.0.0.1:8779'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
HACKATHON_STORAGE_BACKEND = 'local'
HACKATHON_DIRECT_UPLOADS = False
TEMPLATES[0]['OPTIONS']['loaders'] = ['django.template.loaders.filesystem.Loader', 'django.template.loaders.app_directories.Loader']
