"""现场恢复与评委资格影响（H 包，SPEC #[21] / R05 资格变更）。

覆盖三件事：
- 播放助手令牌：更换前先说清影响，确认后才更换，旧令牌立即失效，刷新不会作废它。
- 误结束更正：调度页能给「重新排队」，不用手改数据库。
- 移除评委：先列出会少掉哪些评分、哪些作品会缺评，并留下原因。
"""

import hashlib
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon.models import AuditEntry, Participant, Score, StaffAssignment, StationToken
from tests.hackathon.test_workflows import approved_project, endpoint


pytestmark = pytest.mark.django_db


def make_judge(hackathon, email='judge@example.org', name='评委'):
    user = User.objects.create_user(email=email, password='a-strong-test-password', fullname=name)
    return StaffAssignment.objects.create(hackathon=hackathon, user=user, role='judge')


def prepare_rotation(client, hackathon):
    """点「更换连接令牌」，拿到影响预览与一次性确认凭据。"""
    response = client.post(endpoint(hackathon, 'manage-stage'), {'action': 'token'})
    assert response.status_code == 200
    nonce = BeautifulSoup(response.content, 'html.parser').select_one('input[name="nonce"]')
    assert nonce is not None
    return nonce.get('value', '')


def issue_token(client, hackathon):
    """走完整的「预览影响 → 确认更换」流程，返回这次显示的明文令牌。"""
    nonce = prepare_rotation(client, hackathon)
    response = client.post(
        endpoint(hackathon, 'manage-stage'),
        {'action': 'token_issue', 'nonce': nonce},
        follow=True,
    )
    assert response.status_code == 200
    block = BeautifulSoup(response.content, 'html.parser').select_one('.token-block')
    assert block is not None
    return block.get_text(strip=True)


def score_of(hackathon, project, judge, final=True):
    return Score.objects.create(
        hackathon=hackathon,
        project=project,
        judge=judge.user,
        innovation=8,
        implementation=8,
        impact=7,
        presentation=6,
        final=final,
    )


def prepare_removal(client, hackathon, assignment):
    """点「移除」，先拿到影响预览（这一步也记下「已经看过影响」）。"""
    return client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove_prepare', 'assignment': assignment.pk},
    )


def test_token_rotation_explains_the_impact_before_issuing(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    issue_token(client, hackathon)
    before = StationToken.objects.get(hackathon=hackathon)

    response = client.post(endpoint(hackathon, 'manage-stage'), {'action': 'token'})
    assert response.status_code == 200
    text = response.content.decode()
    assert '旧令牌立即失效' in text
    assert '确认更换令牌' in text
    # 只点了「更换」，旧令牌还在，现场的连接没有被打断。
    assert StationToken.objects.filter(hackathon=hackathon).count() == 1
    assert StationToken.objects.get(hackathon=hackathon).digest == before.digest


def test_issuing_a_token_without_reviewing_the_impact_is_refused(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'manage-stage'),
        {'action': 'token_issue'},
        follow=True,
    )
    assert '请先查看更换令牌的影响' in response.content.decode()
    assert not StationToken.objects.filter(hackathon=hackathon).exists()


def test_a_replayed_token_confirmation_is_refused(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    nonce = prepare_rotation(client, hackathon)
    client.post(endpoint(hackathon, 'manage-stage'), {'action': 'token_issue', 'nonce': nonce})
    issued = StationToken.objects.get(hackathon=hackathon)

    response = client.post(
        endpoint(hackathon, 'manage-stage'),
        {'action': 'token_issue', 'nonce': nonce},
        follow=True,
    )
    assert '请先查看更换令牌的影响' in response.content.decode()
    assert StationToken.objects.get(hackathon=hackathon).digest == issued.digest


def test_issuing_a_token_shows_it_once_with_reconnection_steps(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    nonce = prepare_rotation(client, hackathon)
    response = client.post(
        endpoint(hackathon, 'manage-stage'),
        {'action': 'token_issue', 'nonce': nonce},
        follow=True,
    )
    text = response.content.decode()
    block = BeautifulSoup(response.content, 'html.parser').select_one('.token-block')
    assert block is not None
    token = block.get_text(strip=True)
    station = StationToken.objects.get(hackathon=hackathon)
    assert station.digest == hashlib.sha256(token.encode()).hexdigest()
    assert '请按顺序重新连接' in text
    assert '新令牌已签发' in text


def test_refreshing_the_page_does_not_rotate_the_token_again(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    token = issue_token(client, hackathon)
    station = StationToken.objects.get(hackathon=hackathon)

    again = client.get(endpoint(hackathon, 'manage-stage'))
    assert token not in again.content.decode()
    assert BeautifulSoup(again.content, 'html.parser').select_one('.token-block') is None
    # 刷新没有重新提交表单，刚签发的令牌没有被作废。
    assert StationToken.objects.get(hackathon=hackathon).digest == station.digest


def test_rotating_the_token_invalidates_the_previous_one(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    old_token = issue_token(client, hackathon)
    prepare_rotation(client, hackathon)
    new_token = issue_token(client, hackathon)
    assert new_token != old_token

    location = endpoint(hackathon, 'station-state')
    assert client.get(location, HTTP_AUTHORIZATION=f'Bearer {old_token}').status_code == 403
    assert client.get(location, HTTP_AUTHORIZATION=f'Bearer {new_token}').status_code == 200


def test_reading_the_connection_instructions_keeps_the_current_token(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    issue_token(client, hackathon)
    before = StationToken.objects.get(hackathon=hackathon)
    issued = AuditEntry.objects.filter(hackathon=hackathon, action='签发播放助手令牌').count()

    page = client.get(endpoint(hackathon, 'manage-stage'))
    assert '连接说明与服务地址' in page.content.decode()
    assert StationToken.objects.get(hackathon=hackathon).digest == before.digest
    assert AuditEntry.objects.filter(hackathon=hackathon, action='签发播放助手令牌').count() == issued


def test_connection_status_tells_never_connected_normal_stale_and_expired_apart(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    assert '还没有签发播放助手令牌' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()

    token = issue_token(client, hackathon)
    assert '还没有现场助手连上来' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()

    location = endpoint(hackathon, 'station-state')
    assert client.get(location, HTTP_AUTHORIZATION=f'Bearer {token}').status_code == 200
    station = StationToken.objects.get(hackathon=hackathon)
    assert station.last_seen_at is not None
    assert '最近同步正常' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()

    station.last_seen_at = timezone.now() - timedelta(minutes=10)
    station.save()
    assert '连接中断' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()

    station.expires_at = timezone.now() - timedelta(seconds=1)
    station.save()
    assert '已过期' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()


def test_a_participant_cannot_rotate_the_token_or_remove_a_judge(client, hackathon, participant, organizer_user):
    judge = make_judge(hackathon)
    client.force_login(participant.user)
    assert client.post(endpoint(hackathon, 'manage-stage'), {'action': 'token_issue'}).status_code == 403
    assert (
        client.post(endpoint(hackathon, 'staff'), {'action': 'remove', 'assignment': judge.pk}).status_code == 403
    )
    assert StaffAssignment.objects.filter(pk=judge.pk).exists()
    assert not StationToken.objects.filter(hackathon=hackathon).exists()


def test_a_finished_team_can_be_requeued_from_the_stage_page(client, hackathon, team, organizer_user):
    project = approved_project(hackathon, team)
    services.generate_queue(hackathon, organizer_user)
    services.stage_action(hackathon, organizer_user, project.slot, 'ready')
    services.stage_action(hackathon, organizer_user, project.slot, 'start')
    services.stage_action(hackathon, organizer_user, project.slot, 'finish')

    client.force_login(organizer_user)
    assert '重新排队' in client.get(endpoint(hackathon, 'manage-stage')).content.decode()

    response = client.post(
        endpoint(hackathon, 'manage-stage'),
        {'action': 'reset', 'slot': project.slot.pk, 'note': '误结束，重新演示'},
    )
    assert response.status_code == 302
    project.slot.refresh_from_db()
    assert project.slot.state == 'waiting'
    assert project.slot.note == '误结束，重新演示'
    assert not project.slot.started_at and not project.slot.ended_at


def test_removing_a_judge_shows_the_impact_before_it_happens(client, hackathon, team, organizer_user):
    judge = make_judge(hackathon)
    project = approved_project(hackathon, team)
    score_of(hackathon, project, judge)

    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove_prepare', 'assignment': judge.pk},
    )
    assert response.status_code == 200
    text = response.content.decode()
    assert '不再计入统计' in text
    assert '移除原因' in text
    # 只是预览：预览之后评委还在，评分也还在。
    assert StaffAssignment.objects.filter(pk=judge.pk).exists()
    assert Score.objects.filter(judge=judge.user).exists()


def test_removing_a_judge_without_reviewing_the_impact_keeps_the_role(client, hackathon, team, organizer_user):
    judge = make_judge(hackathon)
    project = approved_project(hackathon, team)
    score_of(hackathon, project, judge)

    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove', 'assignment': judge.pk, 'reason': '临时有事'},
        follow=True,
    )
    assert '请先查看移除影响' in response.content.decode()
    assert StaffAssignment.objects.filter(pk=judge.pk).exists()


def test_removing_a_judge_without_a_reason_is_refused(client, hackathon, organizer_user):
    judge = make_judge(hackathon)
    client.force_login(organizer_user)
    prepare_removal(client, hackathon, judge)
    response = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove', 'assignment': judge.pk},
        follow=True,
    )
    assert '请先填写移除原因' in response.content.decode()
    assert StaffAssignment.objects.filter(pk=judge.pk).exists()


def test_removing_a_judge_records_the_reason_and_drops_the_scores_from_the_count(
    client, hackathon, team, organizer_user
):
    judge = make_judge(hackathon)
    colleague = make_judge(hackathon, 'judge2@example.org', '评委二')
    project = approved_project(hackathon, team)
    score_of(hackathon, project, judge)
    score_of(hackathon, project, colleague)

    client.force_login(organizer_user)
    prepare_removal(client, hackathon, judge)
    response = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove', 'assignment': judge.pk, 'reason': '评委临时无法到场'},
        follow=True,
    )
    assert '不再计入统计' in response.content.decode()
    assert not StaffAssignment.objects.filter(pk=judge.pk).exists()

    entry = AuditEntry.objects.filter(hackathon=hackathon, action='移除工作人员角色').get()
    assert '评委临时无法到场' in entry.detail
    # 影响写在原因前面：操作日志页会截断长文本。
    assert entry.detail.index('影响') < entry.detail.index('原因')
    # 评分本身没有被删掉，只是不再计入统计。
    assert Score.objects.filter(judge=judge.user).exists()
    assert services.results(hackathon)[0]['count'] == 1


def test_the_preview_numbers_match_what_the_statistics_really_do(client, hackathon, team, organizer_user):
    judge = make_judge(hackathon)
    colleague = make_judge(hackathon, 'judge2@example.org', '评委二')
    project = approved_project(hackathon, team)
    score_of(hackathon, project, judge)
    score_of(hackathon, project, colleague)

    impact = services.judge_removal_impact(hackathon, judge)
    assert impact['panel_size'] == 2 and impact['new_panel_size'] == 1
    assert impact['expected_before'] == 2 and impact['expected_after'] == 1
    assert impact['confirmed'] == 1
    assert impact['becoming_incomplete'] == 0
    assert all(item['missing_after'] == 0 for item in impact['items'])


def test_a_judge_who_joined_a_team_keeps_the_preview_consistent(client, hackathon, team, organizer_user):
    judge = make_judge(hackathon)
    colleague = make_judge(hackathon, 'judge2@example.org', '评委二')
    project = approved_project(hackathon, team)
    score_of(hackathon, project, judge)
    score_of(hackathon, project, colleague)
    # 评委先评了分，之后才加入这支队伍：按回避规则，他对这个作品不算应评，
    # 他那份评分本来就没进统计，不能算成「移除后少掉的分数」。
    Participant.objects.create(
        hackathon=hackathon,
        user=judge.user,
        name='评委',
        school='Test University',
        contact='private-contact',
        consent=True,
        team=team,
    )

    impact = services.judge_removal_impact(hackathon, judge)
    assert impact['confirmed'] == 0
    assert impact['becoming_incomplete'] == 0
    assert all(item['missing_after'] == 0 for item in impact['items'])
    # 移除之后统计口径与预览一致：这一份作品仍然完整。
    assert services.results(hackathon)[0]['count'] == 1
    assert services.results(hackathon)[0]['complete'] is True


def test_removing_an_inactive_judge_does_not_shrink_the_effective_panel(client, hackathon, organizer_user):
    judge = make_judge(hackathon)
    make_judge(hackathon, 'judge2@example.org', '评委二')
    judge.user.is_active = False
    judge.user.save()

    impact = services.judge_removal_impact(hackathon, judge)
    assert impact['in_panel'] is False
    assert impact['panel_size'] == 1
    assert impact['new_panel_size'] == 1
    assert impact['confirmed'] == 0

    client.force_login(organizer_user)
    page = prepare_removal(client, hackathon, judge).content.decode()
    assert '1 → 1 人' in page


def test_removing_a_volunteer_needs_no_reason(client, hackathon, organizer_user):
    user = User.objects.create_user(email='volunteer@example.org', password='a-strong-test-password')
    assignment = StaffAssignment.objects.create(hackathon=hackathon, user=user, role='volunteer')

    client.force_login(organizer_user)
    preview = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove_prepare', 'assignment': assignment.pk},
    )
    assert '不参与计分' in preview.content.decode()

    prepare_removal(client, hackathon, assignment)
    response = client.post(
        endpoint(hackathon, 'staff'),
        {'action': 'remove', 'assignment': assignment.pk},
        follow=True,
    )
    assert response.status_code == 200
    assert not StaffAssignment.objects.filter(pk=assignment.pk).exists()
