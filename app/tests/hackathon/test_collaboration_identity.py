"""R08 协作与身份交接：签到动作分离、工单线程与身份、导出稳定依据。"""

import csv
import io
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon.models import (
    AuditEntry,
    Participant,
    StudentAccount,
    SupportTicket,
    TicketMessage,
    VolunteerTask,
)
from tests.hackathon.test_workflows import approved_project, endpoint


pytestmark = pytest.mark.django_db


def make_ticket(hackathon, user, **changes):
    data = {
        'hackathon': hackathon,
        'user': user,
        'subject': '上传卡在 90%',
        'body': '10:20 左右开始，刷新后仍然卡住。',
        'category': 'tech',
    }
    data.update(changes)
    return SupportTicket.objects.create(**data)


def rows_from(response):
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))


def work_reference_link(content, label):
    """工单里那条「作品 / 名单」入口：文案与地址必须落在同一个链接上。"""
    links = [link for link in BeautifulSoup(content, 'html.parser').select('a') if label in link.get_text()]
    assert links, label
    return links[0]


def ticket_replies(content):
    """工单线程里的每一轮发言，按页面上的先后顺序。"""
    soup = BeautifulSoup(content, 'html.parser')
    return [entry.get_text(' ', strip=True) for entry in soup.select('.ticket-reply')]


def test_check_in_and_uncheck_are_separate_actions(client, hackathon, participant, organizer_user):
    client.force_login(organizer_user)
    path = endpoint(hackathon, 'participants')
    data = {'participant': participant.pk, 'action': 'check_in'}
    assert client.post(path, data).status_code == 302
    participant.refresh_from_db()
    assert participant.checked_in

    # 重复点签到不产生第二条记录，也不改写审计。
    participant.refresh_from_db()
    stamped = participant.updated
    client.post(path, data)
    client.post(path, data)
    participant.refresh_from_db()
    assert participant.checked_in
    assert AuditEntry.objects.filter(hackathon=hackathon, action='签到').count() == 1
    # 没有真正改动就不该顶动这条记录。
    assert participant.updated == stamped

    # 撤销只在显式动作下发生。
    client.post(path, {'participant': participant.pk, 'action': 'uncheck'})
    participant.refresh_from_db()
    assert not participant.checked_in
    assert AuditEntry.objects.filter(hackathon=hackathon, action='撤销签到').count() == 1


def test_uncheck_on_a_person_who_never_checked_in_writes_nothing(client, hackathon, participant, organizer_user):
    client.force_login(organizer_user)
    client.post(endpoint(hackathon, 'participants'), {'participant': participant.pk, 'action': 'uncheck'})
    participant.refresh_from_db()
    assert not participant.checked_in
    assert not AuditEntry.objects.filter(hackathon=hackathon, action__in=['签到', '撤销签到']).exists()


def test_check_in_keeps_the_current_search(client, hackathon, participant, organizer_user):
    other = User.objects.create_user(email='quiet@example.org', fullname='旁观者')
    Participant.objects.create(
        hackathon=hackathon, user=other, name='旁观者', school='Test University', contact='x', consent=True
    )
    client.force_login(organizer_user)
    response = client.post(
        f"{endpoint(hackathon, 'participants')}?q=%E5%88%9B%E9%80%A0%E8%80%85",
        {'participant': participant.pk, 'action': 'check_in'},
        follow=True,
    )
    assert response.status_code == 200
    assert b'q=' in response.redirect_chain[0][0].encode()
    content = response.content.decode()
    assert participant.code in content
    assert '旁观者' not in content


def test_unrecognized_action_keeps_state_and_writes_no_audit(client, hackathon, participant, organizer_user):
    client.force_login(organizer_user)
    for payload in ({'participant': participant.pk, 'action': 'toggle'}, {'participant': participant.pk}):
        assert client.post(endpoint(hackathon, 'participants'), payload).status_code == 302
    participant.refresh_from_db()
    assert not participant.checked_in
    assert not AuditEntry.objects.filter(hackathon=hackathon, action__in=['签到', '撤销签到']).exists()


def test_ticket_replies_accumulate_instead_of_overwriting(client, hackathon, user, organizer_user):
    ticket = make_ticket(hackathon, user)
    client.force_login(organizer_user)
    path = endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)
    client.post(path, {'action': 'reply', 'state': 'waiting_user', 'body': '先看一眼浏览器控制台的报错。'}, follow=True)
    client.post(path, {'action': 'reply', 'state': 'resolved', 'body': '问题已修复，感谢配合。'})
    ticket.refresh_from_db()
    assert (ticket.state, ticket.handler) == (SupportTicket.State.RESOLVED, organizer_user)
    assert list(TicketMessage.objects.filter(ticket=ticket).values_list('body', flat=True)) == [
        '先看一眼浏览器控制台的报错。',
        '问题已修复，感谢配合。',
    ]
    assert AuditEntry.objects.filter(hackathon=hackathon, action='处理支持工单').count() == 2

    content = client.get(endpoint(hackathon, 'support')).content.decode()
    assert '先看一眼浏览器控制台的报错。' in content
    assert '问题已修复，感谢配合。' in content
    assert '10:20 左右开始，刷新后仍然卡住。' in content


def test_reply_without_the_explicit_action_saves_nothing(client, hackathon, user, organizer_user):
    ticket = make_ticket(hackathon, user)
    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'support-reply', ticket_id=ticket.pk),
        {'state': 'resolved', 'body': '不该被保存的说明'},
        follow=True,
    )
    assert response.status_code == 200
    assert TicketMessage.objects.filter(ticket=ticket).count() == 0
    ticket.refresh_from_db()
    assert (ticket.state, ticket.handler) == (SupportTicket.State.OPEN, None)


def test_contestant_append_reopens_their_own_ticket(client, hackathon, user, organizer_user):
    ticket = make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'support'),
        {'action': 'append', 'ticket': ticket.pk, 'body': '控制台显示 500，时间是 10:24。'},
        follow=True,
    )
    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.state == SupportTicket.State.OPEN
    note = TicketMessage.objects.get(ticket=ticket)
    assert (note.author, note.from_staff) == (user, False)
    assert AuditEntry.objects.filter(hackathon=hackathon, action='追加工单说明').exists()


def test_append_never_touches_someone_elses_ticket(client, hackathon, user, organizer_user):
    stranger = User.objects.create_user(email='stranger@example.org', fullname='路人')
    ticket = make_ticket(hackathon, stranger, state=SupportTicket.State.WAITING_USER)
    client.force_login(user)
    client.post(endpoint(hackathon, 'support'), {'action': 'append', 'ticket': ticket.pk, 'body': '越权补充'})
    client.post(endpoint(hackathon, 'support'), {'action': 'append', 'ticket': ticket.pk, 'body': ''})
    assert TicketMessage.objects.filter(ticket=ticket).count() == 0
    ticket.refresh_from_db()
    assert ticket.state == SupportTicket.State.WAITING_USER


def test_new_ticket_requires_the_create_action(client, hackathon, user):
    client.force_login(user)
    payload = {'category': 'tech', 'subject': '不该被建出来', 'body': '缺少动作标记。'}
    assert client.post(endpoint(hackathon, 'support'), payload).status_code == 302
    assert not SupportTicket.objects.filter(hackathon=hackathon, subject='不该被建出来').exists()
    assert client.post(endpoint(hackathon, 'support'), {'action': 'new'}).status_code == 200
    assert not SupportTicket.objects.filter(hackathon=hackathon, subject='不该被建出来').exists()
    assert client.post(endpoint(hackathon, 'support'), {'action': 'new', **payload}).status_code == 302
    assert SupportTicket.objects.filter(hackathon=hackathon, subject='不该被建出来').exists()


def test_organizer_sees_stable_identity_label(
    client, hackathon, participant, team, user, organizer_user
):
    make_ticket(hackathon, user)
    client.force_login(organizer_user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    assert f'{participant.name} · {participant.code} · {team.name}' in content


def test_unregistered_account_falls_back_to_a_stable_label(client, hackathon, organizer_user):
    stranger = User.objects.create_user(email='stranger@example.org', fullname='路人')
    make_ticket(hackathon, stranger)
    client.force_login(organizer_user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    # 没报名也要有能区分同名的依据，而不是只留一个姓名。
    assert f'路人 · 账户 #{stranger.pk}' in content


def test_waiting_ticket_becomes_the_workbench_next_step(client, hackathon, team, user):
    ticket = make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    task = response.context['current_task']
    assert task.key == 'ticket_reply'
    assert task.url == endpoint(hackathon, 'support')
    assert f'工单 #{ticket.pk} 等你补充'.encode() in response.content


def test_answering_the_ticket_clears_the_workbench_task(client, hackathon, team, user):
    ticket = make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'home')).context['current_task'].key == 'ticket_reply'
    client.post(endpoint(hackathon, 'support'), {'action': 'append', 'ticket': ticket.pk, 'body': '已按说明重试。'})
    assert client.get(endpoint(hackathon, 'home')).context['current_task'].key != 'ticket_reply'


def test_waiting_ticket_outranks_the_submitted_receipt(client, hackathon, team, user):
    make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    approved_project(hackathon, team)
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'home')).context['current_task'].key == 'ticket_reply'


def test_waiting_ticket_yields_to_registration(client, hackathon, user):
    ticket = make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert response.context['current_task'].key == 'register'
    # 下一步让给报名，但工单在同一页仍然持续可见。
    assert f'工单 #{ticket.pk} 等你补充'.encode() in response.content


def test_waiting_ticket_yields_to_the_submission_deadline(client, hackathon, team, user):
    ticket = make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert response.context['current_task'].key == 'closed'
    assert f'工单 #{ticket.pk} 等你补充'.encode() in response.content


def test_published_results_skip_the_waiting_ticket(client, hackathon, team, user):
    make_ticket(hackathon, user, state=SupportTicket.State.WAITING_USER)
    hackathon.results_published = True
    hackathon.save()
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert response.context['current_task'].key == 'results'
    assert response.context['waiting_ticket'] is None


def test_manage_ticket_kpi_counts_only_what_the_organizer_must_do(client, hackathon, user, organizer_user):
    make_ticket(hackathon, user)
    make_ticket(hackathon, user, subject='等选手补充', state=SupportTicket.State.WAITING_USER)
    make_ticket(hackathon, user, subject='已解决', state=SupportTicket.State.RESOLVED)
    client.force_login(organizer_user)
    overview = client.get(endpoint(hackathon, 'manage'))
    assert overview.context['tickets'] == 1
    assert overview.context['waiting_tickets'] == 1
    # 卡片数字与点进去的列表必须是同一份筛选结果。
    assert f"{endpoint(hackathon, 'support')}?state=open" in overview.content.decode()
    listed = client.get(endpoint(hackathon, 'support'), {'state': 'open'})
    assert len(listed.context['tickets']) == overview.context['tickets']


def test_each_ticket_state_has_its_own_colour(client, hackathon, user, organizer_user):
    for state in SupportTicket.State.values:
        make_ticket(hackathon, user, subject=f'状态 {state}', state=state)
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'support'))
    tones = {ticket.state: ticket.state_tone for ticket in response.context['tickets']}
    assert len(set(tones.values())) == len(SupportTicket.State.values)
    content = response.content.decode()
    for tone in tones.values():
        assert f'class="tag {tone}"' in content


def rounds_query_count(client, path):
    with CaptureQueriesContext(connection) as captured:
        assert client.get(path).status_code == 200
    return len(captured)


def test_thread_cost_does_not_grow_with_the_number_of_rounds(
    client, hackathon, participant, team, user, organizer_user, django_assert_num_queries
):
    ticket = make_ticket(hackathon, user)
    client.force_login(organizer_user)
    detail = endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)
    listing = endpoint(hackathon, 'support')

    def add_rounds(count):
        for index in range(count):
            TicketMessage.objects.create(
                hackathon=hackathon, ticket=ticket, author=organizer_user, from_staff=True, body=f'第 {index} 轮说明'
            )

    add_rounds(3)
    # 先各请求一次，免得首次请求的一次性缓存查询混进基线。
    client.get(detail)
    client.get(listing)
    detail_baseline = rounds_query_count(client, detail)
    list_baseline = rounds_query_count(client, listing)
    add_rounds(9)
    # 详情页和列表页都不该变成「每条发言一次查询」。
    with django_assert_num_queries(detail_baseline):
        client.get(detail)
    with django_assert_num_queries(list_baseline):
        client.get(listing)


def test_tickets_export_carries_identity_and_thread_count(client, hackathon, participant, team, user, organizer_user):
    StudentAccount.objects.create(user=user, student_id='2022XJ0042')
    ticket = make_ticket(hackathon, user)
    project = approved_project(hackathon, team)
    client.force_login(organizer_user)
    client.post(
        endpoint(hackathon, 'support-reply', ticket_id=ticket.pk),
        {'action': 'reply', 'state': 'waiting_user', 'body': '请提供浏览器版本。'},
    )
    rows = rows_from(client.get(endpoint(hackathon, 'export', kind='tickets')))
    header, row = rows[0], rows[1]
    for column in ('工单号', '本届编号', '队伍', '作品', '学号', '状态', '处理人', '最近沟通', '沟通条数'):
        assert column in header
    assert row[header.index('姓名')] == participant.name
    assert row[header.index('本届编号')] == participant.code
    assert row[header.index('队伍')] == team.name
    assert row[header.index('作品')] == project.code
    assert row[header.index('学号')] == '2022XJ0042'
    assert row[header.index('状态')] == '待选手补充'
    assert row[header.index('处理人')] == '组织者'
    assert row[header.index('沟通条数')] == '1'


def test_ticket_reaches_that_teams_project(client, hackathon, participant, team, user, organizer_user):
    ticket = make_ticket(hackathon, user)
    project = approved_project(hackathon, team)
    client.force_login(organizer_user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    # 文案与地址要落在同一个链接上：拆开断言的话，两个位置互换也测不出来。
    link = work_reference_link(content, f'{project.code} · 当前作品')
    assert link['href'] == endpoint(hackathon, 'review', project_id=project.pk)
    reply_page = client.get(endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)).content.decode()
    assert work_reference_link(reply_page, f'{project.code} · 当前作品')['href'] == endpoint(
        hackathon, 'review', project_id=project.pk
    )


def test_ticket_without_a_project_still_finds_the_person(client, hackathon, participant, team, user, organizer_user):
    make_ticket(hackathon, user)
    client.force_login(organizer_user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    link = work_reference_link(content, '在名单中查找本人')
    assert link['href'].startswith(endpoint(hackathon, 'participants'))
    assert f'q={participant.code}' in link['href']


def test_the_player_reads_every_round_of_the_thread_in_order(client, hackathon, user, organizer_user):
    """选手侧要能读到组委会每一轮说明，顺序与写下的一致。"""
    ticket = make_ticket(hackathon, user)
    client.force_login(organizer_user)
    path = endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)
    client.post(path, {'action': 'reply', 'state': 'waiting_user', 'body': '先看一眼浏览器控制台的报错。'})
    client.post(path, {'action': 'reply', 'state': 'resolved', 'body': '问题已修复，感谢配合。'})

    client.force_login(user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    replies = ticket_replies(content)
    # 第一条是开单本身，之后每轮组委会说明各占一条。
    assert len(replies) == 3
    assert '10:20 左右开始，刷新后仍然卡住。' in replies[0]
    assert '先看一眼浏览器控制台的报错。' in replies[1]
    assert '问题已修复，感谢配合。' in replies[2]


def test_the_identity_line_is_on_the_ticket_and_on_every_reply(
    client, hackathon, participant, team, user, organizer_user
):
    """「姓名 · 编号 · 队伍」既出现在工单身份行，也出现在每一轮说明的作者行。"""
    ticket = make_ticket(hackathon, user)
    client.force_login(organizer_user)
    path = endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)
    client.post(path, {'action': 'reply', 'state': 'waiting_user', 'body': '请提供浏览器版本。'})

    content = client.get(endpoint(hackathon, 'support')).content.decode()
    soup = BeautifulSoup(content, 'html.parser')
    identity = f'{participant.name} · {participant.code} · {team.name}'
    # 工单身份行与线程作者行是两处，不能只断其中一个。
    assert identity in [node.get_text(' ', strip=True) for node in soup.select('p > strong')]
    assert identity in [node.get_text(' ', strip=True) for node in soup.select('.ticket-reply strong')]


def test_the_thread_follows_the_writing_order_even_if_the_clock_moves_back(
    client, hackathon, user, organizer_user
):
    """线程按写入顺序排列：系统时钟回拨时也不会把两轮说明倒过来。"""
    ticket = make_ticket(hackathon, user)
    first = TicketMessage.objects.create(
        hackathon=hackathon, ticket=ticket, author=organizer_user, from_staff=True, body='第一轮说明'
    )
    second = TicketMessage.objects.create(
        hackathon=hackathon, ticket=ticket, author=organizer_user, from_staff=True, body='第二轮说明'
    )
    # 模拟时钟回拨：后写入的一条带着更早的时间戳。
    TicketMessage.objects.filter(pk=second.pk).update(created=first.created - timedelta(seconds=1))

    client.force_login(organizer_user)
    content = client.get(endpoint(hackathon, 'support')).content.decode()
    replies = ticket_replies(content)
    assert '第一轮说明' in replies[1]
    assert '第二轮说明' in replies[2]


def test_only_the_first_concurrent_check_in_writes_an_audit(client, hackathon, participant, organizer_user):
    """两个主办同时点签到：只有真正改到状态的那位写审计。"""
    # 两份旧快照正是并发时会话里各自持有的对象。
    first_view = Participant.objects.get(pk=participant.pk)
    second_view = Participant.objects.get(pk=participant.pk)
    assert services.set_check_in(hackathon, organizer_user, first_view, True) is True
    assert services.set_check_in(hackathon, organizer_user, second_view, True) is False
    assert AuditEntry.objects.filter(hackathon=hackathon, action='签到').count() == 1
    participant.refresh_from_db()
    assert participant.checked_in is True


def test_tickets_export_marks_unregistered_openers(client, hackathon, user, organizer_user):
    stranger = User.objects.create_user(email='stranger@example.org', fullname='路人')
    make_ticket(hackathon, stranger)
    same_name = User.objects.create_user(email=None, fullname='路人')
    make_ticket(hackathon, same_name)
    client.force_login(organizer_user)
    rows = rows_from(client.get(endpoint(hackathon, 'export', kind='tickets')))
    header = rows[0]
    by_account = {row[header.index('账户编号')]: row for row in rows[1:]}
    # 未报名、无学号、甚至无邮箱的同名账户，也要靠稳定编号分得开；邮箱只是联系方式。
    assert by_account[f'#{stranger.pk}'][header.index('本届编号')] == ''
    assert by_account[f'#{stranger.pk}'][header.index('队伍')] == '未报名'
    assert by_account[f'#{stranger.pk}'][header.index('邮箱')] == 'stranger@example.org'
    assert by_account[f'#{same_name.pk}'][header.index('姓名')] == '路人'
    assert by_account[f'#{same_name.pk}'][header.index('学号')] == ''
    assert by_account[f'#{same_name.pk}'][header.index('邮箱')] == ''


def test_volunteers_export_splits_reference_from_contact(client, hackathon, organizer_user):
    owner = User.objects.create_user(email='owner@example.org', fullname='赵一')
    backup = User.objects.create_user(email='backup@example.org', fullname='赵二')
    StudentAccount.objects.create(user=owner, student_id='2021XJ0001')
    VolunteerTask.objects.create(
        hackathon=hackathon,
        title='签到台',
        description='负责扫码与引导',
        location='一层大厅',
        owner=owner,
        backup=backup,
        due_at=timezone.now() + timedelta(hours=1),
    )
    client.force_login(organizer_user)
    rows = rows_from(client.get(endpoint(hackathon, 'export', kind='volunteers')))
    header, row = rows[0], rows[1]
    assert header[:4] == ['任务', '负责人', '负责人编号', '负责人邮箱']
    assert row[header.index('负责人编号')] == '2021XJ0001'
    assert row[header.index('负责人邮箱')] == 'owner@example.org'
    # 没有学号的账户用稳定的账户号区分，而不是靠邮箱去撞同名。
    assert row[header.index('备份编号')] == f'#{backup.pk}'
    assert row[header.index('备份')] == '赵二'


def test_review_returns_to_the_same_filter(client, hackathon, team, organizer_user):
    project = approved_project(hackathon, team)
    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'review', project_id=project.pk),
        {
            'state': 'changes',
            'review_note': '缺少实机演示录屏',
            'equipment_checked': 'on',
            'award': '',
            'filter_state': 'changes',
            'filter_reason': 'review',
        },
    )
    assert response.status_code == 302
    assert response['Location'].endswith(f"{endpoint(hackathon, 'submissions')}?state=changes&reason=review")


def test_foreign_filter_values_are_dropped_from_the_return_link(client, hackathon, team, organizer_user):
    project = approved_project(hackathon, team)
    client.force_login(organizer_user)
    response = client.post(
        endpoint(hackathon, 'review', project_id=project.pk),
        {
            'state': 'submitted',
            'review_note': '',
            'equipment_checked': 'on',
            'award': '',
            'filter_state': '../../evil',
            'filter_reason': 'not-a-reason',
        },
    )
    assert response.status_code == 302
    assert '?' not in response['Location']
    page = client.get(endpoint(hackathon, 'submissions'), {'state': '../../evil', 'reason': 'nope'})
    assert page.status_code == 200
    assert (page.context['state'], page.context['reason']) == ('', '')
