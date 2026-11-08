from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_scopes import scope

from eventyay.base.models import Event
from eventyay.hackathon.management.commands.refresh_hackathon_branding import NEW_NAME, OLD_DESCRIPTION, OLD_NAME
from eventyay.hackathon.models import Announcement, Hackathon, Resource


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('translated', [False, True])
def test_refresh_default_branding_is_scoped_and_preserves_custom_content(hackathon, apply, translated):
    hackathon.name = OLD_NAME
    hackathon.description = OLD_DESCRIPTION
    hackathon.save()
    event = hackathon.event
    event.name = {'zh-hans': OLD_NAME, 'en': 'Custom hackathon name'} if translated else OLD_NAME
    event.save()
    announcement = Announcement.objects.create(
        hackathon=hackathon, title=f'欢迎来到{OLD_NAME}工作台', body='自定义正文'
    )
    resource = Resource.objects.create(
        hackathon=hackathon,
        title='第一次参加创客营？从这里开始',
        category='guide',
        summary='自定义摘要',
        body='自定义正文',
    )
    custom = Resource.objects.create(
        hackathon=hackathon, title='往届创客营经验分享', category='learn', summary='保留历史内容', body='不重写作者原文'
    )
    deadlines = [hackathon.registration_deadline, hackathon.team_freeze_at, hackathon.submission_deadline]
    program, slug = hackathon.program, hackathon.slug
    other_event = Event.objects.create(
        organizer=event.organizer,
        slug='other',
        name=OLD_NAME,
        date_from=event.date_from,
        date_to=event.date_to,
        timezone=event.timezone,
        currency=event.currency,
    )
    other = Hackathon.objects.create(
        event=other_event,
        slug='other',
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    with scope(event=other_event):
        other_resource = Resource.objects.create(
            hackathon=other, title=resource.title, category='guide', summary='其他活动', body='其他活动'
        )
    output = StringIO()

    call_command('refresh_hackathon_branding', slug, apply=apply, stdout=output)

    hackathon.refresh_from_db()
    event.refresh_from_db()
    announcement.refresh_from_db()
    resource.refresh_from_db()
    custom.refresh_from_db()
    assert hackathon.name == (NEW_NAME if apply else OLD_NAME)
    expected_name = NEW_NAME if apply else OLD_NAME
    assert event.name.data == (
        {'zh-hans': expected_name, 'en': 'Custom hackathon name'} if translated else expected_name
    )
    assert hackathon.description == (OLD_DESCRIPTION.replace('创客营', '黑客松') if apply else OLD_DESCRIPTION)
    assert announcement.title == f'欢迎来到{NEW_NAME if apply else OLD_NAME}工作台'
    assert resource.title == ('第一次参加黑客松？从这里开始' if apply else '第一次参加创客营？从这里开始')
    assert (resource.summary, resource.body) == ('自定义摘要', '自定义正文')
    assert (custom.title, custom.body) == ('往届创客营经验分享', '不重写作者原文')
    assert hackathon.program == program and hackathon.slug == slug
    assert [hackathon.registration_deadline, hackathon.team_freeze_at, hackathon.submission_deadline] == deadlines
    assert '5 field(s)' in output.getvalue()
    other.refresh_from_db()
    other_event.refresh_from_db()
    with scope(event=other_event):
        other_resource.refresh_from_db()
    assert other.name == OLD_NAME and other.description == OLD_DESCRIPTION
    assert str(other_event.name) == OLD_NAME
    assert other_resource.title == '第一次参加创客营？从这里开始'
    if apply:
        output = StringIO()
        call_command('refresh_hackathon_branding', slug, apply=True, stdout=output)
        assert '0 field(s)' in output.getvalue()


def test_refresh_branding_requires_an_existing_explicit_workspace():
    with pytest.raises(CommandError, match='Workspace not found'):
        call_command('refresh_hackathon_branding', 'missing', apply=True, stdout=StringIO())


def test_refresh_branding_rolls_back_on_a_write_failure(hackathon):
    hackathon.name = OLD_NAME
    hackathon.description = OLD_DESCRIPTION
    hackathon.save()
    event = hackathon.event
    event.name = OLD_NAME
    event.save()
    resource = Resource.objects.create(
        hackathon=hackathon, title='第一次参加创客营？从这里开始', category='guide', summary='指南', body='指南'
    )

    with patch.object(Resource, 'save', side_effect=RuntimeError('Simulated write failure')):
        with pytest.raises(RuntimeError, match='Simulated write failure'):
            call_command('refresh_hackathon_branding', hackathon.slug, apply=True, stdout=StringIO())

    hackathon.refresh_from_db()
    event.refresh_from_db()
    resource.refresh_from_db()
    assert hackathon.name == OLD_NAME and hackathon.description == OLD_DESCRIPTION
    assert str(event.name) == OLD_NAME
    assert resource.title == '第一次参加创客营？从这里开始'
