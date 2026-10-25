from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from eventyay.base.models import Team as OrganizerTeam
from eventyay.hackathon.models import Hackathon
from eventyay.hackathon.permissions import is_organizer


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('copy_organizers', [False, True])
def test_setup_explicitly_restores_event_limited_organizers(hackathon, user, existing, copy_organizers):
    hackathon.program = Hackathon.Program.HACKATHON
    hackathon.save()
    organizer_team = OrganizerTeam.objects.create(
        organizer=hackathon.event.organizer, name='Organizers', can_change_event_settings=True, all_events=False
    )
    organizer_team.members.add(user)
    organizer_team.limit_events.add(hackathon.event)
    volunteer_team = OrganizerTeam.objects.create(
        organizer=hackathon.event.organizer, name='Volunteers', all_events=False
    )
    volunteer_team.limit_events.add(hackathon.event)
    unrelated_team = OrganizerTeam.objects.create(
        organizer=hackathon.event.organizer, name='Another event', can_change_event_settings=True, all_events=False
    )
    options = {
        'organizer': hackathon.event.organizer.slug,
        'event': 'camp',
        'slug': 'camp',
        'stdout': StringIO(),
    }
    if existing:
        call_command('setup_hackathon', **options)
    if copy_organizers:
        options['copy_organizers_from'] = hackathon.slug
    call_command('setup_hackathon', **options)
    camp = Hackathon.objects.get(slug='camp')
    assert is_organizer(user, camp) == copy_organizers
    assert organizer_team.limit_events.filter(pk=hackathon.event_id).exists()
    assert not volunteer_team.limit_events.filter(pk=camp.event_id).exists()
    assert not unrelated_team.limit_events.filter(pk=camp.event_id).exists()
    organizer_team.refresh_from_db()
    assert not organizer_team.all_events
    user.refresh_from_db()
    assert not user.is_administrator
    if copy_organizers:
        call_command('setup_hackathon', **options)
        assert organizer_team.limit_events.count() == 2


@pytest.mark.parametrize('source', ['missing', 'foreign'])
def test_setup_rejects_invalid_permission_source_without_partial_changes(hackathon, source):
    if source == 'foreign':
        source = hackathon.slug
    with pytest.raises(CommandError, match='same organizer'):
        call_command(
            'setup_hackathon',
            organizer='another-organizer',
            event='camp',
            slug='camp',
            copy_organizers_from=source,
            stdout=StringIO(),
        )
    assert not Hackathon.objects.filter(slug='camp').exists()
