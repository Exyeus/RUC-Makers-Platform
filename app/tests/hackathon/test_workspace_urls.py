from io import StringIO
from urllib.parse import urlencode

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.forms.models import model_to_dict
from django.test import Client, RequestFactory
from django.urls import reverse

from eventyay.hackathon.accounts import safe_workspace_next
from eventyay.hackathon.models import Hackathon, StudentAccount
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('apply', [False, True])
def test_workspace_rename_only_changes_slug(hackathon, participant, team, apply):
    hackathon.slug = 'camp'
    hackathon.save(update_fields=['slug'])
    original = model_to_dict(hackathon)
    output = StringIO()
    call_command('rename_hackathon_workspace', hackathon.slug, 'tongzhou26', apply=apply, stdout=output)
    hackathon.refresh_from_db()
    assert model_to_dict(hackathon) == {**original, 'slug': 'tongzhou26' if apply else original['slug']}
    participant.refresh_from_db()
    team.refresh_from_db()
    assert participant.hackathon_id == hackathon.pk and participant.team_id == team.pk
    assert team.hackathon_id == hackathon.pk
    assert endpoint(hackathon, 'home') == f'/hackathon/{hackathon.slug}/'


@pytest.mark.parametrize('target', ['test', 'camp', 'invalid/path', 'a' * 51])
def test_workspace_rename_rejects_conflicts_and_invalid_slugs(hackathon, target):
    call_command(
        'setup_hackathon',
        organizer=hackathon.event.organizer.slug,
        slug='source',
        event='source',
        stdout=StringIO(),
    )
    with pytest.raises(CommandError):
        call_command('rename_hackathon_workspace', 'source', target, apply=True, stdout=StringIO())
    assert Hackathon.objects.filter(slug='source').exists()
    hackathon.refresh_from_db()
    assert hackathon.slug == 'test'


def test_workspace_rename_requires_existing_source():
    with pytest.raises(CommandError, match='Source workspace not found'):
        call_command('rename_hackathon_workspace', 'missing', 'tongzhou26', apply=True, stdout=StringIO())


def test_setup_rejects_reserved_slug_without_creating_event(hackathon):
    with pytest.raises(CommandError, match='reserved'):
        call_command(
            'setup_hackathon',
            organizer=hackathon.event.organizer.slug,
            slug='camp',
            event='reserved',
            stdout=StringIO(),
        )
    assert not Hackathon.objects.filter(slug='camp').exists()
    assert not hackathon.event.organizer.events.filter(slug='reserved').exists()


def test_setup_leaves_an_existing_legacy_workspace_untouched(hackathon):
    hackathon.slug = 'camp'
    hackathon.save(update_fields=['slug'])
    original = model_to_dict(hackathon)
    call_command(
        'setup_hackathon', organizer=hackathon.event.organizer.slug, slug='camp', event='reserved', stdout=StringIO()
    )
    hackathon.refresh_from_db()
    assert model_to_dict(hackathon) == original
    assert not hackathon.event.organizer.events.filter(slug='reserved').exists()


@pytest.mark.parametrize('suffix', ['', 'teams/', 'account/password-reset/confirm/', 'project/uploads/'])
@pytest.mark.parametrize('method', ['get', 'head'])
def test_legacy_workspace_redirect_preserves_path_and_query(client, suffix, method):
    query = urlencode({'keyword': 'Python / AI', 'token': 'synthetic-token', 'next': '/hackathon/tongzhou26/project/'})
    response = getattr(client, method)('/hackathon/camp/' + suffix + '?' + query)
    assert response.status_code == 301
    assert response['Location'] == '/hackathon/tongzhou26/' + suffix + '?' + query


def test_legacy_login_preserves_post_body_and_csrf(hackathon, participant, user):
    hackathon.slug = 'tongzhou26'
    hackathon.save(update_fields=['slug'])
    StudentAccount.objects.create(user=user, student_id='20260001')
    client = Client(enforce_csrf_checks=True)
    old_login = '/hackathon/camp/account/login/'
    response = client.get(old_login, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(endpoint(hackathon, 'student-login'), 301)]
    credentials = {'student_id': '20260001', 'password': 'a-strong-test-password'}
    assert client.post(old_login, credentials, follow=True).status_code == 403
    token = client.cookies['eventyay_csrftoken'].value
    response = client.post(old_login, {**credentials, 'csrfmiddlewaretoken': token}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [
        (endpoint(hackathon, 'student-login'), 308),
        (endpoint(hackathon, 'home'), 302),
    ]
    assert str(client.session['_auth_user_id']) == str(user.pk)
    body = response.content.decode()
    assert '/hackathon/camp/' not in body
    assert 'action="' + endpoint(hackathon, 'logout') + '"' in body


def test_canonical_urls_keep_private_workspace_protected(client, hackathon):
    hackathon.slug = 'tongzhou26'
    hackathon.is_public = False
    hackathon.save(update_fields=['slug', 'is_public'])
    response = client.get('/hackathon/camp/', follow=True)
    assert response.status_code == 404
    assert response.redirect_chain == [(endpoint(hackathon, 'home'), 301)]


def test_setup_defaults_use_current_workspace_slug(hackathon):
    call_command('setup_hackathon', organizer=hackathon.event.organizer.slug, stdout=StringIO())
    created = Hackathon.objects.get(slug='tongzhou26')
    assert created.event.slug == 'tongzhou26'
    assert reverse('hackathon:home', kwargs={'hackathon_slug': created.slug}) == '/hackathon/tongzhou26/'


@pytest.mark.parametrize(
    'value,expected',
    [
        ('/hackathon/camp/project/?from=legacy', '/hackathon/tongzhou26/project/?from=legacy'),
        ('//external.example/hackathon/camp/project/', '/hackathon/tongzhou26/'),
        ('/hackathon/another/project/', '/hackathon/tongzhou26/'),
    ],
)
def test_login_next_maps_legacy_paths_and_rejects_external_or_other_workspaces(hackathon, value, expected):
    hackathon.slug = 'tongzhou26'
    request = RequestFactory().get('/')
    request.hackathon = hackathon
    assert safe_workspace_next(request, value, endpoint(hackathon, 'home')) == expected
