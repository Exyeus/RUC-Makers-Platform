"""Judge seats sign in by a personal link that the organizer can reissue or revoke."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from eventyay.hackathon import judge_links
from eventyay.hackathon.account_models import JudgeInvite
from eventyay.hackathon.models import StaffAssignment
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def token_of(link):
    return parse_qs(urlsplit(link).query)['token'][0]


def test_organizer_creates_named_seats_and_sees_each_link_once(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'judge-links')
    response = client.post(location, {'operation': 'create', 'names': '岳冰莹\n王照颖\n岳冰莹\n'})
    assert response.status_code == 302
    page = client.get(location).content.decode()
    assert page.count('/account/judge-link/?token=') == 2
    assert '/account/judge-link/?token=' not in client.get(location).content.decode()
    judges = StaffAssignment.objects.filter(hackathon=hackathon, role='judge')
    assert sorted(a.user.fullname for a in judges) == ['岳冰莹', '王照颖']
    assert all(not a.user.has_usable_password() for a in judges)
    assert JudgeInvite.objects.filter(revoked_at__isnull=True).count() == 2
    assert all(len(invite.digest) == 64 for invite in JudgeInvite.objects.all())


def test_link_preview_does_not_sign_in_and_post_does(hackathon, organizer_user):
    [seat] = judge_links.create_seats(hackathon, organizer_user, ['张慧涛'])
    token = token_of(seat['url'])
    judge = Client()
    preview = judge.get(endpoint(hackathon, 'judge-link'), {'token': token})
    assert preview.status_code == 200 and '张慧涛' in preview.content.decode()
    assert '_auth_user_id' not in judge.session
    response = judge.post(endpoint(hackathon, 'judge-link'), {'token': token})
    assert response.status_code == 302
    assert str(judge.session['_auth_user_id']) == str(seat['user'].pk)
    assert JudgeInvite.objects.get().use_count == 1


def test_reissue_and_revoke_kill_old_links(client, hackathon, organizer_user):
    [seat] = judge_links.create_seats(hackathon, organizer_user, ['崔英粉'])
    old = token_of(seat['url'])
    new = token_of(judge_links.reissue(hackathon, organizer_user, seat['user']))
    assert judge_links.resolve(hackathon, old) is None
    assert judge_links.resolve(hackathon, new).user == seat['user']
    judge_links.revoke(hackathon, organizer_user, seat['user'])
    assert judge_links.resolve(hackathon, new) is None
    response = Client().post(endpoint(hackathon, 'judge-link'), {'token': new})
    assert response.status_code == 404


def test_removed_judge_role_or_participants_cannot_use_or_manage_links(client, hackathon, organizer_user, user):
    [seat] = judge_links.create_seats(hackathon, organizer_user, ['韩雨潼'])
    StaffAssignment.objects.filter(user=seat['user']).delete()
    assert judge_links.resolve(hackathon, token_of(seat['url'])) is None
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'judge-links')).status_code in {302, 403}
    assert client.post(endpoint(hackathon, 'judge-links'), {'operation': 'create', 'names': 'x'}).status_code in {
        302,
        403,
    }
    assert not StaffAssignment.objects.filter(hackathon=hackathon, role='judge').exists()
