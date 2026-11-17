import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from eventyay.hackathon import identity
from eventyay.hackathon.account_models import AccountSecurity, AdmissionGrant
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db
ORIGIN = 'https://testserver'


@pytest.fixture(autouse=True)
def clean_account_cache(settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()


def form_browser(path):
    browser = Client(enforce_csrf_checks=True)
    response = browser.get(path, secure=True)
    assert response.status_code == 200
    assert 'content="no-referrer"' not in response.content.decode()
    return browser, browser.cookies[settings.CSRF_COOKIE_NAME].value


def test_empty_admission_list_does_not_block_https_registration(hackathon):
    hackathon.admission_required = True
    hackathon.save()
    assert not AdmissionGrant.objects.exists()
    path = endpoint(hackathon, 'student-signup')
    browser, token = form_browser(path)
    response = browser.post(
        path,
        {
            'csrfmiddlewaretoken': token,
            'identifier': 'new-registration@example.org',
            'name': '注册验证',
            'password': 'SecureSignup!2026',
            'password_confirm': 'SecureSignup!2026',
            'consent': 'on',
        },
        secure=True,
        HTTP_REFERER=ORIGIN + path,
    )
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert not AdmissionGrant.objects.exists()


@pytest.mark.parametrize('origin', ['null', 'https://untrusted.example.org'])
def test_bad_origin_is_still_rejected_even_with_valid_csrf_token(hackathon, origin):
    path = endpoint(hackathon, 'student-password-reset-request')
    browser, token = form_browser(path)
    response = browser.post(
        path,
        {'csrfmiddlewaretoken': token, 'identifier': 'absent@example.org'},
        secure=True,
        HTTP_ORIGIN=origin,
        HTTP_REFERER=ORIGIN + path,
    )
    assert response.status_code == 403
    assert len(mail.outbox) == 0


def test_https_reset_form_with_same_origin_referer_works_without_origin(hackathon):
    path = endpoint(hackathon, 'student-password-reset-request')
    browser, token = form_browser(path)
    assert browser.get(path, secure=True)['Referrer-Policy'] in {'same-origin', 'strict-origin-when-cross-origin'}
    response = browser.post(
        path,
        {'csrfmiddlewaretoken': token, 'identifier': 'absent@example.org'},
        secure=True,
        HTTP_REFERER=ORIGIN + path,
    )
    assert response.status_code == 302
    assert len(mail.outbox) == 0


def test_verification_and_unsubscribe_confirmations_keep_https_csrf_working(hackathon, user):
    assert identity.send_action(user, hackathon, 'verify')
    raw = parse_qs(urlsplit(re.search(r'https?://[^\s]+', mail.outbox[-1].body).group()).query)['token'][0]
    path = endpoint(hackathon, 'verify-email') + '?token=' + raw
    browser, token = form_browser(path)
    assert browser.get(path, secure=True)['Referrer-Policy'] in {'same-origin', 'strict-origin-when-cross-origin'}
    assert (
        browser.post(
            path, {'csrfmiddlewaretoken': token, 'token': raw}, secure=True, HTTP_REFERER=ORIGIN + path
        ).status_code
        == 302
    )
    profile = AccountSecurity.objects.get(user=user)
    profile.future_updates = True
    profile.save()
    raw = identity.unsubscribe_token(profile)
    path = reverse('hackathon:unsubscribe-account') + '?token=' + raw
    browser, token = form_browser(path)
    assert (
        browser.post(
            path, {'csrfmiddlewaretoken': token, 'token': raw}, secure=True, HTTP_REFERER=ORIGIN + path
        ).status_code
        == 200
    )
    profile.refresh_from_db()
    assert not profile.future_updates


def test_missing_and_stale_csrf_tokens_remain_rejected(hackathon):
    path = endpoint(hackathon, 'student-password-reset-request')
    browser, token = form_browser(path)
    assert (
        browser.post(path, {'identifier': 'absent@example.org'}, secure=True, HTTP_REFERER=ORIGIN + path).status_code
        == 403
    )
    assert (
        browser.post(
            path,
            {'csrfmiddlewaretoken': 'A' * 32, 'identifier': 'absent@example.org'},
            secure=True,
            HTTP_REFERER=ORIGIN + path,
        ).status_code
        == 403
    )


def test_open_testing_allows_unverified_unbound_accounts_and_close_is_immediate(client, hackathon, user):
    hackathon.admission_required = True
    hackathon.save()
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'resources')).status_code == 302
    hackathon.event.settings.set('hackathon_open_testing', True)
    assert client.get(endpoint(hackathon, 'resources')).status_code == 200
    assert identity.admitted(user, hackathon)
    hackathon.event.settings.set('hackathon_open_testing', False)
    assert client.get(endpoint(hackathon, 'resources')).status_code == 302
    assert not identity.admitted(user, hackathon)
    assert not AdmissionGrant.objects.exists()


def test_only_organizer_can_change_open_testing_mode(client, hackathon, user, organizer_user):
    path = endpoint(hackathon, 'admission-manage')
    client.force_login(user)
    assert client.post(path, {'operation': 'mode', 'mode-open_testing': 'on', 'mode-reason': '越权'}).status_code == 403
    assert not identity.open_testing(hackathon)
    client.force_login(organizer_user)
    assert (
        client.post(path, {'operation': 'mode', 'mode-open_testing': 'on', 'mode-reason': '公开测试'}).status_code
        == 302
    )
    hackathon.refresh_from_db()
    assert identity.open_testing(hackathon)
    assert client.post(path, {'operation': 'mode', 'mode-reason': '恢复选拔'}).status_code == 302
    hackathon.refresh_from_db()
    assert not identity.open_testing(hackathon)


def test_open_testing_never_grants_staff_roles_or_accepts_inactive_or_spam(client, hackathon, user):
    hackathon.admission_required = True
    hackathon.save()
    hackathon.event.settings.set('hackathon_open_testing', True)
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'manage')).status_code == 403
    assert client.get(endpoint(hackathon, 'judging')).status_code == 403
    user.is_spam = True
    user.save()
    assert not identity.admitted(user, hackathon)
    user.is_spam = False
    user.is_active = False
    user.save()
    assert not identity.admitted(user, hackathon)
