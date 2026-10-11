from datetime import timedelta

import pytest
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event, Organizer, User
from eventyay.hackathon.models import Hackathon, Participant, Team, Track


@pytest.fixture
def event(db):
    organizer = Organizer.objects.create(name='Hackathon tests', slug='hack-tests')
    return Event.objects.create(
        organizer=organizer,
        name='Hackathon',
        slug='test',
        date_from=timezone.now(),
        date_to=timezone.now() + timedelta(days=10),
        timezone='Asia/Shanghai',
        currency='CNY',
    )


@pytest.fixture
def hackathon(event):
    now = timezone.now()
    h = Hackathon.objects.create(
        event=event,
        slug='test',
        name='Test Hackathon',
        registration_deadline=now + timedelta(days=2),
        team_freeze_at=now + timedelta(days=3),
        soft_deadline=now + timedelta(days=4),
        submission_deadline=now + timedelta(days=5),
        freeze_at=now + timedelta(days=6),
        is_public=True,
        judging_open=True,
    )
    with scope(event=event):
        yield h


@pytest.fixture
def user(db):
    return User.objects.create_user(email='creator@example.org', password='a-strong-test-password', fullname='创造者')


@pytest.fixture
def organizer_user(db):
    return User.objects.create_user(email='organizer@example.org', is_administrator=True, fullname='组织者')


@pytest.fixture
def participant(hackathon, user):
    return Participant.objects.create(
        hackathon=hackathon,
        user=user,
        name='创造者',
        school='Test University',
        contact='private-contact',
        availability='private-availability',
        consent=True,
    )


@pytest.fixture
def team(hackathon, participant):
    track = Track.objects.create(hackathon=hackathon, name='开放创新')
    team = Team.objects.create(hackathon=hackathon, captain=participant, name='Test Team', track=track)
    participant.team = team
    participant.save()
    return team
