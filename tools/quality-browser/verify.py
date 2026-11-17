import hashlib,json,sys
from pathlib import Path
import django
django.setup()
from django.conf import settings
from django_scopes import scopes_disabled
from eventyay.hackathon.models import Hackathon,Participant,Project
assert settings.DATABASES['default']['NAME'].startswith('ruc_quality_')
with scopes_disabled():
    h=Hackathon.objects.get(slug='quality')
    p=Project.objects.get(hackathon=h)
    members=list(Participant.objects.filter(team=p.team).values_list('name',flat=True))
    assets=[]
    for a in p.assets.all():
        with a.file.open('rb') as stream:
            content=stream.read()
        assets.append({'kind':a.kind,'size':len(content),'sha256':hashlib.sha256(content).hexdigest()})
    expected_slides=hashlib.sha256(b'%PDF-1.7\n' + b'a'*(17*1048576-9)).hexdigest()
    expected_poster=hashlib.sha256(Path(settings.DATA_DIR,'poster.png').read_bytes()).hexdigest()
    if sys.argv[1]=='team': assert set(members)=={'合成队长','合成伙伴'}
    elif sys.argv[1]=='upload': assert any(a['kind']=='slides' and a['size']==17*1048576 and a['sha256']==expected_slides for a in assets)
    elif sys.argv[1]=='submission':
        assert p.state=='submitted',p.state
        assert p.submission_receipt and p.submitted_at
        assert any(a['kind']=='slides' and a['sha256']==expected_slides for a in assets)
        assert any(a['kind']=='poster' and a['sha256']==expected_poster for a in assets)
        assert set(members)=={'合成队长','合成伙伴'}
    print(json.dumps({'team':str(p.team_id),'members':members,'project':str(p.pk),'state':p.state,'receipt':str(p.submission_receipt),'assets':assets},ensure_ascii=False))

