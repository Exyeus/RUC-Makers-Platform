import django
django.setup()
from eventyay.base.models.cache import VersionedModel
VersionedModel._set_cache_version_sync = lambda self: None
from django.core.management import call_command
call_command('runserver','127.0.0.1:8779',use_reloader=False,insecure=True)
