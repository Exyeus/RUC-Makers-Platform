import pytest
from django.test import Client

from eventyay.hackathon.models import StudentAccount, Track
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def test_visitor_landing_uses_event_content(client, hackathon):
    hackathon.name = '本届创客营 <script>alert(1)</script>'
    hackathon.tagline = '把我们的想法变成作品'
    hackathon.description = '本届活动的实际介绍。'
    hackathon.venue = '本届活动的实际场地'
    hackathon.save()
    Track.objects.create(hackathon=hackathon, name='本届赛道', description='本届赛道介绍')

    response = client.get(endpoint(hackathon, 'home'))

    assert response.status_code == 200
    templates = [template.name for template in response.templates]
    assert 'hackathon/landing.html' in templates
    assert 'hackathon/base.html' not in templates
    body = response.content.decode()
    for text in (hackathon.tagline, hackathon.description, hackathon.venue, '本届赛道介绍'):
        assert text in body
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body
    assert '<script>alert(1)</script>' not in body
    for placeholder in ('造物有心', '50,000', '36 小时', 'RUC · SSO', '11.14 - 11.16'):
        assert placeholder not in body


def test_authenticated_home_keeps_workspace(client, hackathon, user):
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    templates = [template.name for template in response.templates]
    assert response.status_code == 200
    assert 'hackathon/home.html' in templates
    assert 'hackathon/base.html' in templates
    assert 'hackathon/landing.html' not in templates
    assert 'landing.css' not in response.content.decode()


def test_landing_login_uses_existing_csrf_protected_flow(hackathon, participant, user):
    StudentAccount.objects.create(user=user, student_id='20261234')
    client = Client(enforce_csrf_checks=True)
    response = client.get(endpoint(hackathon, 'home'))
    assert f'action="{endpoint(hackathon, "student-login")}"' in response.content.decode()
    credentials = {'student_id': '20261234', 'password': 'a-strong-test-password'}
    assert client.post(endpoint(hackathon, 'student-login'), credentials).status_code == 403
    token = client.cookies['eventyay_csrftoken'].value
    response = client.post(endpoint(hackathon, 'student-login'), {**credentials, 'csrfmiddlewaretoken': token})
    assert response.status_code == 302
    assert response.url == endpoint(hackathon, 'home')
    assert str(client.session['_auth_user_id']) == str(user.pk)
