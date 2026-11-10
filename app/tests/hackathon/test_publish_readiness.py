from datetime import timedelta

import pytest
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon.models import AuditEntry, Resource, ScheduleItem, StaffAssignment, Track
from tests.hackathon.test_workflows import approved_project, endpoint


pytestmark = pytest.mark.django_db


def test_opening_the_event_waits_for_a_published_track(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'publish')

    assert client.post(location, {'action': 'hide-event'}).status_code == 302
    hackathon.refresh_from_db()
    assert not hackathon.is_public
    assert AuditEntry.objects.filter(hackathon=hackathon, action='关闭活动公开').exists()

    response = client.post(location, {'action': 'open-event'})
    assert response.status_code == 200
    assert '还没有已发布的赛道，公开活动后选手看不到任何赛题。' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.is_public

    Track.objects.create(hackathon=hackathon, name='机器人赛道', state='published')
    assert client.post(location, {'action': 'open-event'}).status_code == 302
    hackathon.refresh_from_db()
    assert (hackathon.is_public, hackathon.judging_open, hackathon.gallery_open) == (True, True, False)


def test_opening_scoring_and_gallery_each_need_their_own_precondition(client, hackathon, team, organizer_user):
    hackathon.judging_open = False
    hackathon.save()
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'publish')

    response = client.post(location, {'action': 'open-judging'})
    assert '还没有可用评委（账户已停用或标为垃圾的不计入），请先到工作人员页添加评审角色。' in response.content.decode()
    assert '还没有通过审核的作品，评委进场后无队可评。' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.judging_open

    project = approved_project(hackathon, team)
    project.public_consent = False
    project.save()
    StaffAssignment.objects.create(
        hackathon=hackathon, user=User.objects.create_user(email='judge-lead@example.org'), role='judge'
    )
    assert client.post(location, {'action': 'open-judging'}).status_code == 302
    hackathon.refresh_from_db()
    assert (hackathon.judging_open, hackathon.gallery_open, hackathon.results_published) == (True, False, False)

    response = client.post(location, {'action': 'open-gallery'})
    assert '还没有同意公开且已通过审核的作品，展厅开放后只是空页面。' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.gallery_open

    project.public_consent = True
    project.save()
    assert client.post(location, {'action': 'open-gallery'}).status_code == 302
    hackathon.refresh_from_db()
    assert hackathon.gallery_open and not hackathon.results_published
    assert AuditEntry.objects.filter(hackathon=hackathon, action='开启作品展厅').exists()


def test_a_sole_deactivated_judge_does_not_open_scoring(client, hackathon, team, organizer_user):
    judge = User.objects.create_user(email='judge-gone@example.org')
    judge.is_active = False
    judge.save()
    StaffAssignment.objects.create(hackathon=hackathon, user=judge, role='judge')
    approved_project(hackathon, team)
    hackathon.judging_open = False
    hackathon.save()
    client.force_login(organizer_user)

    response = client.post(endpoint(hackathon, 'publish'), {'action': 'open-judging'})
    assert '还没有可用评委' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.judging_open


def test_unknown_publish_action_changes_nothing(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    response = client.post(endpoint(hackathon, 'publish'), {'action': 'open-everything'})
    assert '未知的发布操作，本次未改变任何开关。' in response.content.decode()
    hackathon.refresh_from_db()
    assert (hackathon.is_public, hackathon.gallery_open, hackathon.judging_open, hackathon.results_published) == (
        True,
        False,
        True,
        False,
    )
    assert not AuditEntry.objects.filter(hackathon=hackathon).exists()


def test_closing_a_switch_is_never_blocked(client, hackathon, team, organizer_user):
    approved_project(hackathon, team)
    hackathon.results_published = True
    hackathon.save()
    client.force_login(organizer_user)
    assert client.post(endpoint(hackathon, 'publish'), {'action': 'unpublish-results'}).status_code == 302
    hackathon.refresh_from_db()
    assert (hackathon.results_published, hackathon.gallery_open, hackathon.judging_open) == (False, False, True)


def test_publish_page_lists_switches_and_every_readiness_step(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    Resource.objects.create(hackathon=hackathon, title='待发布的必读', category='guide', summary='摘要', body='正文')
    response = client.get(endpoint(hackathon, 'publish'))
    page_text = response.content.decode()
    assert response.status_code == 200
    for label in ('活动公开', '作品展厅', '评委打分', '成绩公布'):
        assert label in page_text
    assert '生效对象' in page_text
    assert '还没有同意公开且已通过审核的作品，展厅开放后只是空页面。' in page_text
    assert page_text.count('去处理') == 7
    assert '尚未填写：简介、场地' in page_text
    assert '还没有已发布赛道，选手无法选择方向。' in page_text
    assert '还有 1 条内容仍是草稿，发布后不会公开。' in page_text
    for name in ('settings', 'content', 'staff', 'results'):
        assert endpoint(hackathon, name) in page_text


def test_readiness_page_links_each_drift_item_to_its_editor(client, hackathon, organizer_user):
    now = timezone.now()
    item = ScheduleItem.objects.create(
        hackathon=hackathon,
        title='提前批路演',
        starts_at=now - timedelta(days=3),
        ends_at=now - timedelta(days=2),
        state='published',
    )
    hackathon.event.date_to = now - timedelta(days=10)
    hackathon.event.save()
    client.force_login(organizer_user)

    page_text = client.get(endpoint(hackathon, 'publish')).content.decode()
    assert '项与新的活动区间不一致' in page_text
    assert '落在新的活动区间之外' in page_text
    assert '改这条日程' in page_text
    assert endpoint(hackathon, 'content-edit', kind='schedule', item_id=item.pk) in page_text


def test_date_drift_names_every_offender_and_links_to_the_right_editor(hackathon):
    now = timezone.now()
    ScheduleItem.objects.create(
        hackathon=hackathon,
        title='提前批路演',
        starts_at=now - timedelta(days=3),
        ends_at=now - timedelta(days=2),
        state='published',
    )
    ScheduleItem.objects.create(
        hackathon=hackathon, title='草稿日程', starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=2)
    )
    hackathon.event.date_to = now - timedelta(days=10)
    hackathon.event.save()

    drift = services.date_drift(hackathon)
    offenders = {row['label']: row for row in drift}
    assert '日程「提前批路演」' in offenders
    assert '草稿日程' not in ' '.join(offenders)
    schedule_row = offenders['日程「提前批路演」']
    item = ScheduleItem.objects.get(title='提前批路演')
    assert schedule_row['url'] == endpoint(hackathon, 'content-edit', kind='schedule', item_id=item.pk)
    assert schedule_row['url_label'] == '改这条日程'

    hackathon.event.date_to = now + timedelta(days=10)
    hackathon.event.save()
    hackathon.soft_deadline = now + timedelta(days=6)
    hackathon.submission_deadline = now + timedelta(days=4)
    hackathon.save()
    offenders = {row['label']: row['detail'] for row in services.date_drift(hackathon)}
    assert offenders['作品提交截止'].startswith('早于「建议提交时间」')
    assert all(row['url'] for row in services.date_drift(hackathon))


def test_settings_page_warns_about_drift_without_moving_any_date(client, hackathon, organizer_user):
    now = timezone.now()
    hackathon.event.date_to = now - timedelta(days=1)
    hackathon.event.save()
    client.force_login(organizer_user)
    page_text = client.get(endpoint(hackathon, 'settings')).content.decode()
    assert '改期后与新的活动区间不一致' in page_text
    assert '系统不会自动平移任何时间' in page_text
    hackathon.refresh_from_db()
    assert hackathon.submission_deadline > now
