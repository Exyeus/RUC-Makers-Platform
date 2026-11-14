"""普通组队的申请闭环、单人队长的退路与个人草稿找回（G 包，SPEC #[6]/[7]/[9]）。"""

from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon.models import CommunityPost, JoinRequest, Participant, Project
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def make_person(hackathon, email, name):
    user = User.objects.create_user(email=email, password='a-strong-test-password', fullname=name)
    return Participant.objects.create(
        hackathon=hackathon,
        user=user,
        name=name,
        school='Test University',
        contact='private-contact',
        consent=True,
    )


def apply_to(client, hackathon, team, message='我会做前端'):
    return client.post(
        endpoint(hackathon, 'team-action'),
        {'action': 'apply', 'team': team.pk, 'message': message},
    )


def test_applicant_sees_the_result_after_the_captain_answers(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    client.force_login(applicant.user)
    assert apply_to(client, hackathon, team).status_code == 302
    application = JoinRequest.objects.get(team=team, participant=applicant)
    assert application.state == 'pending'

    page = client.get(endpoint(hackathon, 'teams'))
    mine = BeautifulSoup(page.content, 'html.parser').select_one('#my-applications')
    assert mine is not None
    assert team.name in mine.text
    assert '等待队长回复' in mine.text

    client.force_login(team.captain.user)
    client.post(endpoint(hackathon, 'team-action'), {'action': 'decline', 'application': application.pk})
    application.refresh_from_db()
    assert application.state == 'declined'

    client.force_login(applicant.user)
    mine = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser').select_one('#my-applications')
    assert '已被婉拒' in mine.text


def test_applicant_can_withdraw_a_pending_application(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    client.force_login(applicant.user)
    apply_to(client, hackathon, team)
    application = JoinRequest.objects.get()

    client.force_login(team.captain.user)
    assert list(client.get(endpoint(hackathon, 'teams')).context['applications']) == [application]

    client.force_login(applicant.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'withdraw', 'application': application.pk})
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.state == 'withdrawn'

    client.force_login(team.captain.user)
    assert list(client.get(endpoint(hackathon, 'teams')).context['applications']) == []


def test_only_the_applicant_can_withdraw(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    bystander = make_person(hackathon, 'bystander@example.org', '无关的人')
    client.force_login(applicant.user)
    apply_to(client, hackathon, team)
    application = JoinRequest.objects.get()

    client.force_login(bystander.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'withdraw', 'application': application.pk})
    assert response.status_code == 403
    application.refresh_from_db()
    assert application.state == 'pending'


def test_pending_application_replaces_the_second_apply_button(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    client.force_login(applicant.user)
    apply_to(client, hackathon, team)

    soup = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser')
    card = soup.select_one('.team-card')
    assert '已申请，等待队长回复' in card.text
    assert not card.select('input[value="apply"]')


def test_declined_application_can_be_renewed(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    client.force_login(applicant.user)
    apply_to(client, hackathon, team)
    application = JoinRequest.objects.get()

    client.force_login(team.captain.user)
    client.post(endpoint(hackathon, 'team-action'), {'action': 'decline', 'application': application.pk})

    client.force_login(applicant.user)
    apply_to(client, hackathon, team, message='我改了方向，还是想加入')
    application.refresh_from_db()
    assert application.state == 'pending'
    assert application.message == '我改了方向，还是想加入'


@pytest.mark.parametrize("recommendations", [False, True])
def test_solo_captain_can_dissolve_the_team_and_start_over(client, hackathon, team, recommendations):
    hackathon.matching_enabled = recommendations
    hackathon.matching_roster_enabled = False
    hackathon.save()
    assert team.members.count() == 1
    client.force_login(team.captain.user)
    soup = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser')
    assert soup.select_one('input[value="dissolve"]') is not None

    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'dissolve'})
    assert response.status_code == 302
    team.refresh_from_db()
    assert team.state == 'dissolved'
    assert team.recruiting is False
    captain = Participant.objects.get(pk=team.captain_id)
    assert captain.team_id is None

    # 解散后这支队伍不再出现在招募列表里，本人可以重新申请别人。
    other_team = team
    other_team.name = '另一支队伍'
    other_team.state = 'recruiting'
    other_team.recruiting = True
    other_team.save()
    client.force_login(team.captain.user)
    soup = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser')
    assert '解散队伍' not in soup.text
    assert apply_to(client, hackathon, other_team).status_code == 302


def test_dissolving_a_team_closes_its_pending_applications(client, hackathon, team):
    applicant = make_person(hackathon, 'applicant@example.org', '申请人')
    client.force_login(applicant.user)
    apply_to(client, hackathon, team)
    application = JoinRequest.objects.get()

    client.force_login(team.captain.user)
    client.post(endpoint(hackathon, 'team-action'), {'action': 'dissolve'})
    application.refresh_from_db()
    assert application.state == 'declined'


def test_captain_with_members_still_needs_a_handover(client, hackathon, team):
    member = make_person(hackathon, 'member@example.org', '队友')
    member.team = team
    member.save()
    client.force_login(team.captain.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'dissolve'}, follow=True)
    assert '还有队友' in response.content.decode()
    team.refresh_from_db()
    assert team.state != 'dissolved'


def test_team_with_a_project_cannot_be_dissolved(client, hackathon, team):
    Project.objects.create(hackathon=hackathon, team=team, title='作品', summary='s', state='draft')
    client.force_login(team.captain.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'dissolve'}, follow=True)
    assert '已经建过作品' in response.content.decode()
    team.refresh_from_db()
    assert team.state != 'dissolved'


def test_dissolving_is_blocked_after_the_roster_freeze(client, hackathon, team):
    hackathon.team_freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    client.force_login(team.captain.user)
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'dissolve'}, follow=True)
    assert '冻结' in response.content.decode()
    team.refresh_from_db()
    assert team.state != 'dissolved'


def test_own_draft_is_findable_and_stays_private(client, hackathon, participant):
    CommunityPost.objects.create(
        hackathon=hackathon,
        author=participant,
        kind='idea',
        title='我的草稿',
        body='还没写完',
        is_public=False,
    )
    client.force_login(participant.user)
    soup = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser')
    mine = soup.select_one('#my-posts')
    assert mine is not None
    assert '我的草稿' in mine.text
    assert '草稿 · 仅自己可见' in mine.text
    assert '我的草稿' not in soup.select_one('#community').text

    other = make_person(hackathon, 'other@example.org', '别人')
    client.force_login(other.user)
    assert '我的草稿' not in client.get(endpoint(hackathon, 'teams')).content.decode()
