import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from eventyay.base.models import Team as OrganizerTeam
from eventyay.hackathon.models import StudentAccount
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db
PASSWORD = 'Navigation!Password2026'


@pytest.fixture(autouse=True)
def auth_cache(settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()


def test_organizer_login_uses_hub_and_opens_management(client, hackathon, organizer_user):
    organizer_user.set_password(PASSWORD)
    organizer_user.save()
    location = endpoint(hackathon, 'organizer-login')
    response = client.get(location)
    assert response.status_code == 200
    assert 'hackathon/organizer_auth.html' in [template.name for template in response.templates]
    assert 'hackathon/base.html' in [template.name for template in response.templates]
    response = client.post(location, {'email': organizer_user.email, 'password': PASSWORD})
    assert response.status_code == 302
    assert response.url == endpoint(hackathon, 'manage')
    assert str(client.session['_auth_user_id']) == str(organizer_user.pk)
    assert client.get(response.url).status_code == 200


def test_protected_management_uses_organizer_login(client, hackathon):
    response = client.get(endpoint(hackathon, 'manage'))
    assert response.status_code == 302
    assert response.url.startswith(endpoint(hackathon, 'organizer-login') + '?next=')


def test_event_organizer_login_keeps_management_navigation(client, hackathon, user):
    team = OrganizerTeam.objects.create(
        organizer=hackathon.event.organizer, name='Camp organizers', can_change_event_settings=True, all_events=False
    )
    team.members.add(user)
    team.limit_events.add(hackathon.event)
    response = client.post(
        endpoint(hackathon, 'organizer-login'), {'email': user.email, 'password': 'a-strong-test-password'}
    )
    assert response.url == endpoint(hackathon, 'manage')
    assert client.get(response.url).status_code == 200
    response = client.get(endpoint(hackathon, 'home'))
    assert response.context['is_organizer']
    assert 'manage' in [item[0] for item in response.context['staff_navigation']]
    assert 'aria-label="主办方后台"' in response.content.decode()
    assert client.get(endpoint(hackathon, 'organizer-login')).url == endpoint(hackathon, 'manage')


def test_old_login_link_redirects_into_hub(client, hackathon):
    target = endpoint(hackathon, 'submissions') + '?state=submitted'
    response = client.get(reverse('auth.login'), {'next': target})
    assert response.status_code == 302
    assert response.url.startswith(endpoint(hackathon, 'organizer-login') + '?next=')
    assert client.get(response.url).status_code == 200


def test_email_participant_keeps_participant_permissions(client, hackathon, participant, user):
    response = client.post(
        endpoint(hackathon, 'organizer-login'), {'email': user.email, 'password': 'a-strong-test-password'}
    )
    assert response.url == endpoint(hackathon, 'home')
    assert client.get(endpoint(hackathon, 'manage')).status_code == 403
    body = client.get(endpoint(hackathon, 'home')).content.decode()
    assert 'aria-label="主办方后台"' not in body
    assert 'href="' + endpoint(hackathon, 'organizer-login') + '"' in body


def test_wrong_password_stays_in_hub(client, hackathon, organizer_user):
    response = client.post(
        endpoint(hackathon, 'organizer-login'), {'email': organizer_user.email, 'password': 'incorrect'}
    )
    assert response.status_code == 200
    assert '邮箱或密码不正确' in response.content.decode()
    assert '_auth_user_id' not in client.session


@pytest.mark.parametrize('attribute', ['is_active', 'is_spam'])
def test_disallowed_accounts_cannot_login(client, hackathon, user, attribute):
    setattr(user, attribute, attribute != 'is_active')
    user.save()
    response = client.post(
        endpoint(hackathon, 'organizer-login'), {'email': user.email, 'password': 'a-strong-test-password'}
    )
    assert response.status_code == 200
    assert '_auth_user_id' not in client.session


def test_organizer_two_factor_remains_required(client, hackathon, organizer_user):
    organizer_user.set_password(PASSWORD)
    organizer_user.require_2fa = True
    organizer_user.save()
    response = client.post(
        endpoint(hackathon, 'organizer-login'), {'email': organizer_user.email, 'password': PASSWORD}
    )
    assert response.url.startswith(reverse('auth.login.2fa'))
    assert '_auth_user_id' not in client.session
    assert client.session['eventyay_auth_2fa_user'] == organizer_user.pk


def test_logout_requires_post_and_clears_session(client, hackathon, user):
    client.force_login(user)
    location = endpoint(hackathon, 'logout')
    assert client.get(location).status_code == 405
    assert '_auth_user_id' in client.session
    assert '退出登录' in client.get(endpoint(hackathon, 'home')).content.decode()
    response = client.post(location)
    assert response.url == endpoint(hackathon, 'student-login')
    assert '_auth_user_id' not in client.session
    assert response.cookies['sso_token']['max-age'] == 0
    assert client.get(endpoint(hackathon, 'project')).status_code == 302
    assert '已退出登录' in client.get(response.url).content.decode()


def test_logout_csrf_rejects_forged_request(hackathon, user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    assert client.post(endpoint(hackathon, 'logout')).status_code == 403
    assert '_auth_user_id' in client.session


def test_participant_can_switch_to_organizer_account(client, hackathon, user):
    client.force_login(user)
    login_url = endpoint(hackathon, 'organizer-login')
    response = client.get(login_url)
    assert response.status_code == 200
    assert '退出并切换账户' in response.content.decode()
    assert '_auth_user_id' in client.session
    response = client.post(endpoint(hackathon, 'logout'), {'next': login_url})
    assert response.url == login_url
    assert '_auth_user_id' not in client.session


def test_login_and_logout_reject_external_redirects(client, hackathon, participant, user):
    location = endpoint(hackathon, 'organizer-login') + '?next=https://example.org'
    response = client.post(location, {'email': user.email, 'password': 'a-strong-test-password'})
    assert response.url == endpoint(hackathon, 'home')
    response = client.post(endpoint(hackathon, 'logout'), {'next': '//example.org'})
    assert response.url == endpoint(hackathon, 'student-login')


def test_student_organizer_login_opens_management(client, hackathon, organizer_user):
    organizer_user.set_password(PASSWORD)
    organizer_user.save()
    StudentAccount.objects.create(user=organizer_user, student_id='20268888')
    response = client.post(endpoint(hackathon, 'student-login'), {'student_id': '20268888', 'password': PASSWORD})
    assert response.url == endpoint(hackathon, 'manage')


def test_login_links_stay_within_hub(client, hackathon):
    response = client.get(endpoint(hackathon, 'student-login'))
    assert endpoint(hackathon, 'organizer-login') in response.content.decode()
    assert 'href="' + reverse('auth.login') + '?next=' not in response.content.decode()
