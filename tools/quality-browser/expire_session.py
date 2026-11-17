import sys
import django
django.setup()
from django.conf import settings
from django.contrib.sessions.models import Session
assert settings.DATABASES['default']['NAME'].startswith('ruc_quality_')
key = sys.stdin.read().strip()
assert key and Session.objects.filter(session_key=key).delete()[0] == 1
