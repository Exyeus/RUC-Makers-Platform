import json
from datetime import timedelta
from pathlib import Path
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_scopes import scope
from eventyay.base.models import Event, Organizer, User
from eventyay.base.models.cache import VersionedModel
from eventyay.hackathon.models import Hackathon, Participant, Project, StudentAccount, Team, Track, MatchingProfile
VersionedModel._set_cache_version_sync = lambda self: None
assert settings.DATABASES['default']['NAME'].startswith('ruc_quality_')
assert not Hackathon.objects.exists()
now = timezone.now()
o = Organizer.objects.create(name='QA synthetic', slug='quality')
e = Event.objects.create(organizer=o,name='QA synthetic',slug='quality', date_from=now,date_to=now+timedelta(days=7),timezone='Asia/Shanghai',currency='CNY')
h = Hackathon.objects.create(event=e,slug='quality',name='隔离验收',is_public=True,matching_enabled=True,registration_deadline=now+timedelta(days=1),team_freeze_at=now+timedelta(days=2),soft_deadline=now+timedelta(days=3),submission_deadline=now+timedelta(days=4),freeze_at=now+timedelta(days=5))
with scope(event=e):
    people=[]
    for role, name in [('captain','合成队长'),('member','合成伙伴'),('outsider','合成外部用户')]:
        u=User.objects.create_user(email=f'{role}@example.invalid',fullname=name,password='QA-local-only-2026!')
        StudentAccount.objects.create(user=u,student_id=f'QA{role.upper()}')
        p=Participant.objects.create(hackathon=h,user=u,name=name,school='Synthetic',contact='Synthetic',consent=True,looking_for_team=True,bio='提供 Python 编程与界面测试')
        MatchingProfile.objects.create(hackathon=h,participant=p,opt_in=True,visibility='event')
        c=Client(); c.force_login(u)
        state={'cookies':[{'name':settings.SESSION_COOKIE_NAME,'value':c.cookies[settings.SESSION_COOKIE_NAME].value,'domain':'127.0.0.1','path':'/','httpOnly':True,'secure':False,'sameSite':'Lax'}],'origins':[]}
        Path(settings.DATA_DIR,f'{role}.json').write_text(json.dumps(state))
        people.append(p)
    organizer=User.objects.create_user(email='organizer@example.invalid',fullname='合成主办',password='QA-local-only-2026!',is_administrator=True)
    c=Client(); c.force_login(organizer)
    state={'cookies':[{'name':settings.SESSION_COOKIE_NAME,'value':c.cookies[settings.SESSION_COOKIE_NAME].value,'domain':'127.0.0.1','path':'/','httpOnly':True,'secure':False,'sameSite':'Lax'}],'origins':[]}
    Path(settings.DATA_DIR,'organizer.json').write_text(json.dumps(state))
    track=Track.objects.create(hackathon=h,name='QA Track')
    team=Team.objects.create(hackathon=h,captain=people[0],track=track,name='合成验收队',intro='寻找 Python 伙伴',needs='Python',recruiting=True)
    people[0].team=team; people[0].save()
    project=Project.objects.create(hackathon=h,team=team,title='合成验收作品',summary='Synthetic only',description='Synthetic only',repository_url='https://example.invalid/repo',demo_url='https://example.invalid/demo',backup_url='https://example.invalid/backup',demo_instructions='打开合成 Demo',checklist=True)
    Path(settings.DATA_DIR,'fixture.json').write_text(json.dumps({'team':str(team.pk),'project':str(project.pk),'captain':str(people[0].pk),'member':str(people[1].pk)}))

from PIL import Image
Image.new('RGB',(32,32),'white').save(Path(settings.DATA_DIR,'poster.png'))
