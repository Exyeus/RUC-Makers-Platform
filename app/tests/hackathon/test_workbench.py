from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.utils import timezone

from eventyay.base.models import Event
from eventyay.hackathon import services
from eventyay.hackathon.models import (
    Announcement,
    DemoSlot,
    Hackathon,
    Project,
    Resource,
    ResourceRead,
    ScheduleItem,
    Score,
    StaffAssignment,
)
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def overview(client, hackathon, user):
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    return response, soup


def required_resource(hackathon, **kwargs):
    return Resource.objects.create(
        hackathon=hackathon,
        category='guide',
        title='提交必读',
        summary='材料要求',
        body='提交说明',
        required=True,
        **kwargs,
    )


def test_unregistered_next_task_and_deadlines(client, hackathon, user):
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'register'
    assert soup.select_one('.ruc-button--primary')['href'] == endpoint(hackathon, 'register')
    deadlines = soup.select('.ruc-inline-deadline time, .ruc-deadline-value time')
    assert len(deadlines) == 2
    assert deadlines[0]['datetime'] == deadlines[1]['datetime']
    assert deadlines[0].text == deadlines[1].text
    assert 'Asia/Shanghai' in soup.select_one('.ruc-deadline-value').text
    assert deadlines[0].text == timezone.localtime(
        hackathon.submission_deadline, ZoneInfo(hackathon.event.timezone)
    ).strftime('%Y 年 %-m 月 %-d 日 %H:%M')
    assert soup.select_one('a[aria-label="个人资料"]')['href'] == endpoint(hackathon, 'register')
    assert len(soup.select('.ruc-button--primary')) == 1


def test_no_team_recommends_team_before_reading(client, hackathon, participant, user):
    required_resource(hackathon)
    response, _ = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'team'
    assert response.context['current_task'].url == endpoint(hackathon, 'teams')


def test_unread_is_personal_and_never_blocks_project(client, hackathon, team, user):
    first = required_resource(hackathon, position=0)
    second = required_resource(hackathon, position=1)
    ResourceRead.objects.create(hackathon=hackathon, resource=first, user=user)
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'read'
    assert response.context['current_task'].url == endpoint(hackathon, 'resource', resource_id=second.pk)
    assert response.context['read_count'] == 1
    assert response.context['progress'] == 50
    assert soup.select_one('details.ruc-completed').get('open') is None
    assert any(a['href'] == endpoint(hackathon, 'project') for a in soup.select('.ruc-actions a'))
    # The workbench must not introduce a new reading gate into the existing editor.
    assert client.get(endpoint(hackathon, 'project')).status_code == 200
    ResourceRead.objects.create(hackathon=hackathon, resource=second, user=user)
    response, _ = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'project'


def test_no_required_reading_is_complete(client, hackathon, team, user):
    response, _ = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'project'
    assert response.context['progress'] == 75


@pytest.mark.parametrize(
    'state,submitted,key',
    [
        ('draft', False, 'project'),
        ('queued', False, 'queued'),
        ('queued', True, 'queued'),
        ('changes', True, 'changes'),
        ('submitted', True, 'submitted'),
        ('approved', True, 'submitted'),
    ],
)
def test_project_status_drives_next_task(client, hackathon, team, user, state, submitted, key):
    Project.objects.create(
        hackathon=hackathon,
        team=team,
        title='真实作品',
        state=state,
        submitted_at=timezone.now() if submitted else None,
    )
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == key
    assert soup.select_one('.ruc-button--primary')['href'] == endpoint(hackathon, 'project')
    assert '真实作品' in soup.select_one('#project-title').parent.text


@pytest.mark.parametrize(
    'submitted,state,key,route',
    [
        (False, 'draft', 'closed', 'support'),
        (True, 'submitted', 'closed', 'project'),
        (False, 'queued', 'queued', 'project'),
    ],
)
def test_deadline_overrides_reading_and_submission_actions(client, hackathon, team, user, submitted, state, key, route):
    required_resource(hackathon)
    Project.objects.create(
        hackathon=hackathon,
        team=team,
        title='截止测试',
        state=state,
        submitted_at=timezone.now() if submitted else None,
    )
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == key
    assert soup.select_one('.ruc-button--primary')['href'] == endpoint(hackathon, route)
    assert '准备作品材料' not in soup.select_one('.ruc-button--primary').text


@pytest.mark.parametrize('phase,key', [('registration', 'registration-closed'), ('team', 'team-closed')])
def test_closed_registration_and_frozen_team(client, hackathon, participant, user, phase, key):
    if phase == 'registration':
        participant.delete()
        hackathon.registration_deadline = timezone.now() - timedelta(seconds=1)
    else:
        hackathon.matching_frozen_at = timezone.now()
    hackathon.save()
    response, _ = overview(client, hackathon, user)
    assert response.context['current_task'].key == key
    assert response.context['current_task'].url == endpoint(hackathon, 'support')


def test_results_take_priority_and_preserve_records(client, hackathon, team, user):
    project = Project.objects.create(hackathon=hackathon, team=team, title='已提交作品', submitted_at=timezone.now())
    hackathon.results_published = True
    hackathon.save()
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'results'
    assert soup.select_one('.ruc-button--primary')['href'] == endpoint(hackathon, 'results')
    project.refresh_from_db()
    assert project.submitted_at is not None


def test_empty_content_long_titles_and_real_links(client, hackathon, team, user):
    response, soup = overview(client, hackathon, user)
    assert '暂无通知' in soup.text
    assert '暂无后续日程' in soup.text
    title = '很长的真实通知标题 <script>alert(1)</script>' * 3
    Announcement.objects.create(hackathon=hackathon, title=title, body='可信通知内容', pinned=True)
    now = timezone.now()
    ScheduleItem.objects.create(
        hackathon=hackathon, title='下一项活动', starts_at=now, ends_at=now + timedelta(hours=1)
    )
    response, soup = overview(client, hackathon, user)
    assert soup.select_one('.ruc-notice h3').text == title
    assert '<script>alert(1)</script>' not in response.content.decode()
    assert '下一项活动' in soup.select_one('.ruc-schedule').text
    assert not soup.select('a a, a button, button a')
    ids = [tag['id'] for tag in soup.select('[id]')]
    assert len(ids) == len(set(ids))
    assert all(a.get('href') not in ('', '#') for a in soup.select('a'))
    assert soup.select_one('[data-nav-toggle]')['aria-controls'] == 'sidebar'
    assert soup.select_one('[data-nav-close]')
    assert soup.select_one('a[aria-current="page"]').text.strip() == '参赛总览'


def test_other_event_reading_does_not_affect_progress(client, hackathon, team, user):
    required_resource(hackathon)
    event = Event.objects.create(
        organizer=hackathon.event.organizer,
        name='Other',
        slug='other',
        date_from=timezone.now(),
        currency='CNY',
    )
    other = Hackathon.objects.create(
        event=event,
        slug='other',
        name='Other',
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    resource = required_resource(other)
    ResourceRead.objects.create(hackathon=other, resource=resource, user=user)
    response, _ = overview(client, hackathon, user)
    assert response.context['read_count'] == 0
    assert response.context['required_count'] == 1
    assert response.context['current_task'].key == 'read'


@pytest.mark.parametrize('role,route', [('judge', 'judge-live'), ('volunteer', 'tasks')])
def test_role_workspaces_remain_available(client, hackathon, user, role, route):
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role=role)
    _, soup = overview(client, hackathon, user)
    assert any(a['href'] == endpoint(hackathon, route) for a in soup.select('#sidebar a'))


@pytest.mark.parametrize('route', ['schedule', 'announcements'])
def test_populated_public_information_retains_content_and_escaping(client, hackathon, user, route):
    title = '活动安排与材料说明' * 8
    content = '请阅读安排\n<script>alert("notice")</script>'
    Announcement.objects.create(hackathon=hackathon, title=title, body=content, pinned=True)
    ScheduleItem.objects.create(
        hackathon=hackathon,
        title=title,
        description=content,
        starts_at=timezone.now() - timedelta(minutes=5),
        ends_at=timezone.now() + timedelta(hours=1),
        location='教学楼共享空间',
    )
    client.force_login(user)
    response = client.get(endpoint(hackathon, route))
    soup = BeautifulSoup(response.content, 'html.parser')
    assert response.status_code == 200
    assert title in soup.select_one('main').text
    assert '<script>alert("notice")</script>' in soup.select_one('main').text
    assert not soup.select('main script')
    if route == 'schedule':
        assert soup.select_one('.schedule-row.happening')
        assert '教学楼共享空间' in soup.select_one('.schedule-row').text
    else:
        assert '置顶通知' in soup.select_one('.announcement').text


@pytest.mark.parametrize('route', [
    'schedule', 'announcements', 'resources', 'teams', 'project', 'gallery', 'stage', 'support',
])
def test_workspace_theme_and_navigation_survive_page_changes(client, hackathon, team, user, route):
    client.force_login(user)
    response = client.get(endpoint(hackathon, route))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    assert 'ruc-app' in soup.body['class']
    styles = [link['href'] for link in soup.select('link[rel="stylesheet"]')]
    assert any('workbench.css' in href for href in styles)
    assert any('workbench-pages.css' in href for href in styles)
    assert not any('/brand.css' in href for href in styles)
    current = soup.select('#sidebar a[aria-current="page"]')
    assert len(current) == 1
    assert current[0]['href'] == endpoint(hackathon, route)
    for target in ['home', 'schedule', 'announcements', 'resources', 'teams', 'project']:
        assert any(a['href'] == endpoint(hackathon, target) for a in soup.select('#sidebar a'))
    assert not soup.select('#sidebar a[aria-current="page"][href="' + endpoint(hackathon, 'home') + '"]')


def test_resource_detail_keeps_guide_navigation(client, hackathon, user):
    resource = required_resource(hackathon)
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'resource', resource_id=resource.pk))
    soup = BeautifulSoup(response.content, 'html.parser')
    assert response.status_code == 200
    assert soup.select_one('#sidebar a[aria-current="page"]')['href'] == endpoint(hackathon, 'resources')
    assert '提交说明' in soup.select_one('.article-body').text


@pytest.mark.parametrize('route', ['register', 'team-edit'])
def test_profile_and_team_forms_use_shared_workspace(client, hackathon, team, user, route):
    client.force_login(user)
    response = client.get(endpoint(hackathon, route))
    soup = BeautifulSoup(response.content, 'html.parser')
    assert response.status_code == 200
    assert 'ruc-app' in soup.body['class']
    assert soup.select_one('form input[name="csrfmiddlewaretoken"]')
    assert soup.select_one('.form-field input')
    target = 'home' if route == 'register' else 'teams'
    assert soup.select_one('#sidebar a[aria-current="page"]')['href'] == endpoint(hackathon, target)


@pytest.mark.parametrize('route', ['manage', 'participants', 'submissions', 'manage-stage', 'content', 'settings'])
def test_organizer_pages_share_theme_and_keep_role_navigation(client, hackathon, organizer_user, route):
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, route))
    soup = BeautifulSoup(response.content, 'html.parser')
    assert response.status_code == 200
    assert 'ruc-app' in soup.body['class']
    assert soup.select_one('#sidebar a[aria-current="page"]')['href'] == endpoint(hackathon, route)


def test_public_workspace_uses_theme_but_entrance_keeps_its_layout(client, hackathon):
    response = client.get(endpoint(hackathon, 'schedule'))
    assert response.status_code == 200
    assert 'workbench-pages.css' in response.content.decode()
    for route in ['home', 'student-login', 'organizer-login', 'judge-login']:
        response = client.get(endpoint(hackathon, route), follow=True)
        assert response.status_code == 200
        soup = BeautifulSoup(response.content, 'html.parser')
        assert 'entry-page' in soup.body['class']
        assert 'workbench.css' not in response.content.decode()


def test_fullscreen_stage_keeps_display_mode(client, hackathon, team):
    # 路演队列只列仍"已通过"的作品，故候场作品需为 approved 才会进入 upcoming。
    project = Project.objects.create(hackathon=hackathon, team=team, title='候场作品', state='approved')
    DemoSlot.objects.create(hackathon=hackathon, project=project, position=1, state='waiting')
    response = client.get(endpoint(hackathon, 'stage'), {'display': '1'})
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    assert {'ruc-app', 'display-mode'} <= set(soup.body['class'])
    assert soup.select_one('.stage-board')
    assert response.context['data']['upcoming'][0]['project'] == project.title
    assert response.context['data']['upcoming'][0]['team'] == team.name


@pytest.mark.parametrize('lock', ['live', 'done', 'score'])
def test_locked_project_keeps_receipt_without_promising_edits(client, hackathon, team, user, lock):
    project = Project.objects.create(
        hackathon=hackathon,
        team=team,
        title='已锁定作品',
        state='submitted',
        submitted_at=timezone.now(),
    )
    if lock == 'score':
        Score.objects.create(
            hackathon=hackathon,
            project=project,
            judge=user,
            final=True,
            innovation=8,
            implementation=8,
            impact=8,
            presentation=8,
        )
    else:
        DemoSlot.objects.create(hackathon=hackathon, project=project, position=1, state=lock)
    with pytest.raises(ValidationError):
        services.require_project_editable(project)
    response, soup = overview(client, hackathon, user)
    assert response.context['current_task'].key == 'submitted'
    assert '作品页当前状态' in soup.select_one('.ruc-task-description').text
    assert '截止前由队长修改' not in soup.text
    assert soup.select_one('.ruc-button--primary')['href'] == endpoint(hackathon, 'project')
