import re
from datetime import timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import urls as hackathon_urls
from eventyay.hackathon.calendar import schedule_heatmap, schedule_timeline
from eventyay.hackathon.models import ScheduleItem, StaffAssignment, SupportTicket
from eventyay.hackathon.views import navigation_column
from tests.hackathon.test_workflows import endpoint


WORKBENCH_CSS = Path(__file__).resolve().parents[2] / 'eventyay' / 'static' / 'hackathon' / 'workbench.css'
# 工作区页面：这些路由渲染完整页面，侧栏要为能打开它的身份保留入口。
# 访问级别来自 event_view 装饰器（permissions.py），别名页沿用所属栏目。
# 加分页按设计留在导航之外（doc/ui-workbench.md），因此不在这份清单里。
WORKSPACE_PAGES = (
    'home',
    'teams',
    'project',
    'resources',
    'schedule',
    'announcements',
    'gallery',
    'results',
    'stage',
    'support',
    'manage',
    'participants',
    'submissions',
    'manage-stage',
    'publish',
    'content',
    'staff',
    'settings',
    'scoring-rules',
    'matching-operations',
    'tasks',
    'judging',
    'judge-live',
)


pytestmark = pytest.mark.django_db
PASSWORD = 'a-strong-test-password'


@pytest.fixture(autouse=True)
def local_cache(settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()


@pytest.fixture
def judge_user(hackathon):
    user = User.objects.create_user(email='rolejudge@example.org', password=PASSWORD, fullname='评委')
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='judge')
    return user


@pytest.fixture
def volunteer_user(hackathon):
    user = User.objects.create_user(email='rolevolunteer@example.org', password=PASSWORD, fullname='志愿者')
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='volunteer')
    return user


def routes(response):
    return [item[0] for item in response.context['navigation']]


def primary(response):
    return [item[0] for item in response.context['navigation_groups'][0][1]]


def mobile_bar(response):
    nav = BeautifulSoup(response.content, 'html.parser').select_one('nav.mobile-nav')
    return [span.get_text(strip=True) for span in nav.select('a span, button span')]


def browser_title(response):
    match = re.search(r'<title>(.*?)</title>', response.content.decode(), re.S)
    return ' '.join(match.group(1).split())


def heading_text(response):
    body = response.content.decode()
    heading = re.search(r'<h1[^>]*>(.*?)</h1>', body, re.S).group(1)
    return ' '.join(re.sub(r'<[^>]+>', '', heading).split())


def page_access_levels():
    """每个路由的访问级别，取自 event_view 装饰器。"""
    return {
        pattern.name: getattr(pattern.callback, 'access', 'public')
        for pattern in hackathon_urls.event_patterns
    }


def columns_the_identity_can_open(identity, roles=()):
    """侧栏必须保留的入口：路由表 × 访问级别，别名页沿用所属栏目。"""
    levels = page_access_levels()
    allowed = {'public', 'login'}
    if identity == 'judge':
        allowed.add('judge')
    if identity == 'volunteer':
        allowed.add('volunteer')
    if identity == 'organizer':
        allowed |= {'organizer', 'volunteer'}
        if 'judge' in roles:
            allowed.add('judge')
    return {navigation_column(name) for name in WORKSPACE_PAGES if levels[name] in allowed}


def css_rules(css):
    cleaned = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return [(selector.strip(), body) for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', cleaned)]


def css_rule(css, selector):
    for candidate, body in css_rules(css):
        if candidate == selector:
            return body
    return ''


def test_anonymous_navigation_offers_only_public_entries(client, hackathon):
    response = client.get(endpoint(hackathon, 'home'))
    assert response.status_code == 200
    assert set(routes(response)) == {
        'home',
        'schedule',
        'announcements',
        'resources',
        'gallery',
        'stage',
        'teams',
        'support',
        'judge-login',
        'results',
    }
    assert len(primary(response)) <= 6
    assert response.context['identities'] == []


def test_participant_navigation_leads_with_their_own_work(client, hackathon, participant, user):
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert primary(response) == ['home', 'teams', 'project']
    assert set(routes(response)) == {
        'home',
        'teams',
        'project',
        'schedule',
        'announcements',
        'support',
        'resources',
        'gallery',
        'stage',
        'results',
    }
    for forbidden in ('manage', 'participants', 'submissions', 'judging', 'judge-live', 'tasks', 'scoring-rules'):
        assert forbidden not in routes(response)
    assert response.context['identities'] == []


def test_judge_navigation_leads_with_review_and_stays_out_of_management(client, hackathon, judge_user):
    client.force_login(judge_user)
    response = client.get(endpoint(hackathon, 'judging'))
    assert primary(response) == ['judging', 'judge-live', 'results']
    for forbidden in (
        'manage',
        'participants',
        'submissions',
        'publish',
        'staff',
        'settings',
        'scoring-rules',
        'tasks',
    ):
        assert forbidden not in routes(response)
    assert 'support' in routes(response)


def test_volunteer_navigation_leads_with_tasks(client, hackathon, volunteer_user):
    client.force_login(volunteer_user)
    response = client.get(endpoint(hackathon, 'tasks'))
    assert primary(response)[0] == 'tasks'
    for forbidden in ('manage', 'participants', 'judging', 'judge-live', 'scoring-rules'):
        assert forbidden not in routes(response)
    assert 'support' in routes(response)


def test_organizer_navigation_keeps_every_management_entry(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'manage'))
    assert response.context['is_organizer']
    assert primary(response) == ['manage', 'participants', 'submissions', 'manage-stage', 'results', 'publish']
    assert len(primary(response)) <= 6
    for expected in (
        'content',
        'staff',
        'settings',
        'scoring-rules',
        'matching-operations',
        'tasks',
        'support',
        'home',
        'schedule',
        'announcements',
    ):
        assert expected in routes(response)
    assert 'judging' not in routes(response)


def test_organizer_who_also_judges_keeps_both_workbenches(client, hackathon, organizer_user):
    StaffAssignment.objects.create(hackathon=hackathon, user=organizer_user, role='judge')
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'manage'))
    assert 'judging' in routes(response) and 'judge-live' in routes(response)
    assert 'manage' in routes(response)


@pytest.mark.parametrize(
    'fixture_name,entry',
    [
        ('judge_user', 'judging'),
        ('volunteer_user', 'tasks'),
        ('organizer_user', 'manage'),
        ('user', 'home'),
    ],
)
def test_every_sidebar_entry_opens_for_its_role(client, hackathon, participant, request, fixture_name, entry):
    account = request.getfixturevalue(fixture_name)
    client.force_login(account)
    navigation = client.get(endpoint(hackathon, entry)).context['navigation']
    for name, _title, _icon, location in navigation:
        response = client.get(location)
        assert response.status_code == 200, f'{fixture_name} cannot open {name}: {response.status_code}'


def test_identity_switcher_lists_only_held_identities(client, hackathon, organizer_user):
    StaffAssignment.objects.create(hackathon=hackathon, user=organizer_user, role='judge')
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'manage'))
    assert [item['label'] for item in response.context['identities']] == ['选手', '评委', '志愿者', '主办']
    assert response.context['working_identity'] == '主办'
    assert 'aria-label="当前工作身份"' in response.content.decode()


def test_identity_switcher_follows_the_page_not_the_account(client, hackathon, judge_user):
    client.force_login(judge_user)
    assert client.get(endpoint(hackathon, 'judging')).context['working_identity'] == '评委'
    response = client.get(endpoint(hackathon, 'home'))
    assert response.context['working_identity'] == '选手'
    assert [item['label'] for item in response.context['identities']] == ['选手', '评委']


def test_sidebar_follows_the_switched_identity(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    management = client.get(endpoint(hackathon, 'manage'))
    assert management.context['navigation_groups'][0][0] == '我的待办'
    player_view = client.get(endpoint(hackathon, 'home'))
    assert player_view.context['navigation_groups'][0][0] == '我的参赛'
    assert player_view.context['working_identity'] == '选手'
    assert player_view.context['page_title'] == '参赛总览'
    assert 'manage' not in routes(player_view)
    assert 'aria-label="主办方后台"' in player_view.content.decode()
    back = next(item for item in player_view.context['identities'] if item['key'] == 'organizer')
    assert client.get(back['url']).context['navigation_groups'][0][0] == '我的待办'


def test_organizer_previewing_volunteer_work_keeps_a_way_back(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'tasks'))
    assert response.context['navigation_groups'][0][0] == '我的工作'
    assert response.context['working_identity'] == '志愿者'
    assert '主办' in [item['label'] for item in response.context['identities']]


@pytest.mark.parametrize(
    'fixture_name,entry,expected',
    [
        ('organizer_user', 'manage', ['总控', '作品', '路演', '成绩', '更多']),
        ('organizer_user', 'home', ['首页', '组队', '作品', '日程', '更多']),
        ('judge_user', 'judging', ['路演', '评审', '日程', '指南', '更多']),
        ('volunteer_user', 'tasks', ['任务', '日程', '指南', '支持', '更多']),
        ('user', 'home', ['首页', '组队', '作品', '日程', '更多']),
    ],
)
def test_mobile_bar_follows_the_working_identity(
    client, hackathon, participant, request, fixture_name, entry, expected
):
    client.force_login(request.getfixturevalue(fixture_name))
    response = client.get(endpoint(hackathon, entry))
    assert mobile_bar(response) == expected
    assert all(len(label) <= 4 for label in mobile_bar(response))


def test_mobile_bar_for_anonymous_visitors(client, hackathon):
    assert mobile_bar(client.get(endpoint(hackathon, 'schedule'))) == ['首页', '组队', '作品', '日程', '更多']


def test_switching_identity_does_not_grant_permissions(client, hackathon, judge_user):
    client.force_login(judge_user)
    switcher = client.get(endpoint(hackathon, 'home')).context['identities']
    assert [item['key'] for item in switcher] == ['participant', 'judge']
    for item in switcher:
        client.get(item['url'])
    assert client.get(endpoint(hackathon, 'manage')).status_code == 403
    assert client.get(endpoint(hackathon, 'scoring-rules')).status_code == 403


@pytest.mark.parametrize(
    'name,label',
    [
        ('manage', '活动总控'),
        ('participants', '报名与签到'),
        ('submissions', '作品检查'),
        ('manage-stage', '路演调度'),
        ('results', '评审与成绩'),
        ('publish', '发布准备'),
        ('content', '内容管理'),
        ('staff', '成员与权限'),
        ('settings', '活动设置'),
        ('scoring-rules', '算分规则'),
        ('matching-operations', '组队撮合'),
    ],
)
def test_management_headings_are_task_names_in_nav_heading_and_title(client, hackathon, organizer_user, name, label):
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, name))
    assert response.status_code == 200
    assert response.context['page_title'] == label
    body = response.content.decode()
    heading = re.search(r'<h1[^>]*>(.*?)</h1>', body, re.S).group(1)
    assert label in re.sub(r'<[^>]+>', '', heading)
    assert f'{label} · {hackathon.name}' in body


@pytest.mark.parametrize(
    'name,label',
    [
        ('schedule', '活动日程'),
        ('announcements', '通知公告'),
        ('resources', '学习与指南'),
        ('gallery', '作品展厅'),
        ('stage', '路演现场'),
        ('support', '帮助与支持'),
        ('project', '我的作品'),
    ],
)
def test_participant_headings_are_task_names(client, hackathon, participant, user, name, label):
    client.force_login(user)
    response = client.get(endpoint(hackathon, name))
    assert response.status_code == 200
    assert response.context['page_title'] == label
    heading = re.search(r'<h1[^>]*>(.*?)</h1>', response.content.decode(), re.S).group(1)
    assert label in re.sub(r'<[^>]+>', '', heading)


def test_team_square_heading_matches_the_navigation_label(client, hackathon, participant, user):
    anonymous = client.get(endpoint(hackathon, 'teams'))
    assert '组队广场' in re.sub(
        r'<[^>]+>', '', re.search(r'<h1[^>]*>(.*?)</h1>', anonymous.content.decode(), re.S).group(1)
    )
    client.force_login(user)
    signed_in = client.get(endpoint(hackathon, 'teams'))
    assert signed_in.context['page_title'] == '我的队伍'
    assert '我的队伍' in re.sub(
        r'<[^>]+>', '', re.search(r'<h1[^>]*>(.*?)</h1>', signed_in.content.decode(), re.S).group(1)
    )


@pytest.mark.parametrize(
    'fixture_name,name',
    [
        ('user', 'home'),
        ('user', 'teams'),
        ('user', 'schedule'),
        ('user', 'gallery'),
        ('user', 'resources'),
        ('user', 'support'),
        ('user', 'stage'),
        ('user', 'announcements'),
        ('user', 'project'),
        ('organizer_user', 'manage'),
        ('organizer_user', 'participants'),
        ('organizer_user', 'submissions'),
        ('organizer_user', 'manage-stage'),
        ('organizer_user', 'publish'),
        ('organizer_user', 'content'),
        ('organizer_user', 'staff'),
        ('organizer_user', 'results'),
        ('organizer_user', 'tasks'),
        ('organizer_user', 'matching-operations'),
        ('organizer_user', 'scoring-rules'),
        ('judge_user', 'judging'),
        ('judge_user', 'judge-live'),
    ],
)
def test_internal_links_carry_no_outward_arrow(client, hackathon, participant, request, fixture_name, name):
    client.force_login(request.getfixturevalue(fixture_name))
    body = client.get(endpoint(hackathon, name)).content.decode()
    external = {reverse('auth.login'), reverse('presale:events.upcoming')}
    for attrs, text in re.findall(r'<a\b([^>]*)>(.*?)</a>', body, re.S):
        visible = re.sub(r'<span[^>]*aria-hidden="true"[^>]*>.*?</span>', '', text, flags=re.S)
        if '↗' not in visible:
            continue
        href = re.search(r'href="([^"]*)"', attrs)
        assert 'target="_blank"' in attrs or (href and href.group(1) in external), visible


def test_long_term_promotion_is_collapsed_by_default(client, hackathon, participant, user):
    client.force_login(user)
    body = client.get(endpoint(hackathon, 'home')).content.decode()
    assert '<details class="sidebar-note">' in body
    assert '查看孵化与往届资源' in body


def test_timeline_carries_the_same_items_and_deadlines_as_the_grid(hackathon):
    due = hackathon.submission_deadline.replace(minute=30, second=0, microsecond=0)
    hackathon.submission_deadline = due
    hackathon.save()
    item = ScheduleItem.objects.create(
        hackathon=hackathon,
        title='同小时答疑',
        starts_at=due - timedelta(minutes=45),
        ends_at=due + timedelta(hours=1),
        location='创客教室',
        state='published',
    )
    timeline = schedule_timeline(hackathon)
    assert [entry['starts_at'] for entry in timeline] == sorted(entry['starts_at'] for entry in timeline)
    assert len([entry for entry in timeline if entry['level'] == 'deadline']) == 5
    entry = next(entry for entry in timeline if entry['title'] == '同小时答疑')
    assert entry['level'] == 'event' and entry['location'] == '创客教室'
    assert entry['ends_at'] == item.ends_at.astimezone(timezone.get_fixed_timezone(480))
    assert entry['starts_at'].utcoffset() == timedelta(hours=8)
    grid = ' '.join(
        cell['label'] for day in schedule_heatmap(hackathon) for cell in day['hours'] if cell['level'] != 'empty'
    )
    for entry in timeline:
        assert entry['title'] in grid


def test_home_offers_the_calendar_as_readable_text(client, hackathon, participant, user):
    starts = timezone.now() + timedelta(days=1)
    ScheduleItem.objects.create(
        hackathon=hackathon, title='开幕式', starts_at=starts, ends_at=starts + timedelta(hours=1), state='published'
    )
    client.force_login(user)
    body = client.get(endpoint(hackathon, 'home')).content.decode()
    assert 'calendar-timeline' in body
    assert '开幕式' in body and '作品提交截止' in body
    assert body.index('calendar-timeline') < body.index('hour-calendar-scroll')


def test_schedule_leads_with_the_list_and_keeps_the_grid_optional(client, hackathon, participant, user):
    starts = timezone.now() + timedelta(days=1)
    ScheduleItem.objects.create(
        hackathon=hackathon, title='开幕式', starts_at=starts, ends_at=starts + timedelta(hours=1), state='published'
    )
    client.force_login(user)
    body = client.get(endpoint(hackathon, 'schedule')).content.decode()
    assert body.index('schedule-list') < body.index('hour-calendar')
    assert '<details class="ruc-calendar">' in body
    assert 'calendar-timeline' not in body
    assert '开幕式' in body


def test_ticket_reply_keeps_the_organizer_workbench(client, hackathon, organizer_user):
    """主办打开工单回复页时仍然是主办，不是选手。"""
    client.force_login(organizer_user)
    ticket = SupportTicket.objects.create(
        hackathon=hackathon,
        user=organizer_user,
        subject='材料一直停在排队',
        body='换了网络也重试了三次。',
        category='tech',
    )
    response = client.get(endpoint(hackathon, 'support-reply', ticket_id=ticket.pk))
    assert response.status_code == 200
    assert response.context['working_identity_key'] == 'organizer'
    assert response.context['working_identity'] == '主办'
    assert response.context['navigation_groups'][0][0] == '我的待办'
    assert 'manage' in routes(response)
    assert response.context['navigation_active'] == 'support'


@pytest.mark.parametrize(
    'fixture_name,entry,identity',
    [
        ('user', 'home', 'participant'),
        ('judge_user', 'judging', 'judge'),
        ('volunteer_user', 'tasks', 'volunteer'),
        ('organizer_user', 'manage', 'organizer'),
    ],
)
def test_sidebar_covers_every_page_the_identity_can_open(
    client, hackathon, participant, request, fixture_name, entry, identity
):
    """每个身份能打开的页面都在侧栏保留入口，因此没有页面失去高亮。"""
    account = request.getfixturevalue(fixture_name)
    client.force_login(account)
    response = client.get(endpoint(hackathon, entry))
    roles = set(
        StaffAssignment.objects.filter(hackathon=hackathon, user=account).values_list('role', flat=True)
    )
    missing = columns_the_identity_can_open(identity, roles) - set(routes(response))
    assert not missing, f'{identity} 侧栏缺少入口：{sorted(missing)}'


def test_judge_and_volunteer_keep_the_shared_pages_in_the_sidebar(client, hackathon, judge_user, volunteer_user):
    """评委与志愿者回到路演现场、组队与作品页时，侧栏仍然有路可回。"""
    for account, entry in ((judge_user, 'judging'), (volunteer_user, 'tasks')):
        client.force_login(account)
        response = client.get(endpoint(hackathon, entry))
        for shared in ('stage', 'teams', 'project'):
            assert shared in routes(response)
        assert response.context['navigation_active'] in routes(response)


@pytest.mark.parametrize(
    'fixture_name,name,kwargs,task',
    [
        ('user', 'register', {}, '个人资料 · 让队友认识你'),
        ('user', 'team-edit', {}, '管理我的队伍'),
        ('user', 'matching', {}, '匹配推荐'),
        ('user', 'bonus', {}, '加分规则'),
        ('organizer_user', 'content-new', {'kind': 'announcement'}, '新增通知'),
        ('organizer_user', 'judge-new', {}, '创建评委账户'),
        ('organizer_user', 'task-new', {}, '安排志愿任务'),
    ],
)
def test_browser_title_uses_the_page_task_name(
    client, hackathon, participant, team, request, fixture_name, name, kwargs, task
):
    """浏览器标题用这一页自己的任务名，别名只决定侧栏高亮哪一栏。"""
    client.force_login(request.getfixturevalue(fixture_name))
    response = client.get(endpoint(hackathon, name, **kwargs))
    assert response.status_code == 200
    assert response.context['page_title'] == task
    assert heading_text(response) == task
    assert browser_title(response) == f'{task} · {hackathon.name}'


def test_ticket_reply_uses_the_ticket_subject_as_its_task_name(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    ticket = SupportTicket.objects.create(
        hackathon=hackathon,
        user=organizer_user,
        subject='材料一直停在排队',
        body='换了网络也重试了三次。',
        category='tech',
    )
    response = client.get(endpoint(hackathon, 'support-reply', ticket_id=ticket.pk))
    assert response.context['page_title'] == '材料一直停在排队'
    assert heading_text(response) == '材料一直停在排队'
    assert browser_title(response) == f'材料一直停在排队 · {hackathon.name}'


def test_password_reset_pages_keep_the_campaign_title(client, hackathon, user):
    """登录与找回密码页保留「活动名 · 黑客松」，不退回裸活动名。"""
    response = client.get(endpoint(hackathon, 'student-password-reset-request'))
    assert response.status_code == 200
    assert browser_title(response) == f'{hackathon.name} · 黑客松'

    user.pw_reset_token = 'reset-token-for-tests'
    user.pw_reset_time = timezone.now()
    user.save(update_fields=['pw_reset_token', 'pw_reset_time'])
    confirm = client.get(f'{endpoint(hackathon, "student-password-reset-confirm")}?token=reset-token-for-tests')
    assert confirm.status_code == 200
    assert browser_title(confirm) == f'{hackathon.name} · 黑客松'


def test_timeline_uses_the_kind_the_organizer_entered(hackathon, client, participant, user):
    """时间线的类型用主办填写的 ScheduleItem.kind，不写死成「活动」。"""
    starts = timezone.now() + timedelta(days=1)
    ScheduleItem.objects.create(
        hackathon=hackathon,
        title='路演彩排',
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        kind='路演',
        state='published',
    )
    entry = next(entry for entry in schedule_timeline(hackathon) if entry['title'] == '路演彩排')
    assert entry['kind'] == '路演'

    client.force_login(user)
    home = client.get(endpoint(hackathon, 'home')).content.decode()
    schedule = client.get(endpoint(hackathon, 'schedule')).content.decode()
    timeline = re.search(r'<ol class="calendar-timeline">(.*?)</ol>', home, re.S).group(1)
    assert '路演彩排' in timeline
    assert re.search(r'<span class="tag">\s*路演\s*</span>', timeline)
    assert '活动' not in timeline
    assert re.search(r'<span class="tag">\s*路演\s*</span>', schedule)


def test_workbench_layer_covers_the_small_action_links():
    """工作区层给动作链接补足字号与 44px 触达，并让时间段用等宽数字。"""
    css = WORKBENCH_CSS.read_text(encoding='utf-8')
    for selector in (
        '.ruc-app .metric > a',
        '.ruc-app a.tag',
        '.ruc-app .judge-team-picker summary',
        '.ruc-app .ruc-notice h3 a',
    ):
        body = css_rule(css, selector)
        assert body, f'{selector} 缺少 workbench 层覆盖'
        assert 'min-height: 2.75rem' in body, f'{selector} 触达高度不足 44px'
    for selector in ('.ruc-app .metric > a', '.ruc-app a.tag'):
        assert 'font-size: var(--ruc-small-size)' in css_rule(css, selector), f'{selector} 字号低于 14px'
    tabular = [selector for selector, body in css_rules(css) if 'tabular-nums' in body]
    assert any('.schedule-time' in selector for selector in tabular), '时间段没有使用等宽数字'
