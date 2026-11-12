"""工单线程迁移的回填：旧 reply 搬成一条发言，谁说的、什么时候说的都要对。"""

import importlib

import pytest
from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader


pytestmark = pytest.mark.django_db

MIGRATION = '0016_ticket_thread'
PREVIOUS = '0015_content_publish_state'


def thread_state_apps():
    """回到「TicketMessage 已建好、reply 还没删」那一刻的模型状态。"""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    state = loader.project_state(('hackathon', PREVIOUS))
    for operation in loader.disk_migrations[('hackathon', MIGRATION)].operations:
        if isinstance(operation, migrations.RunPython):
            break
        operation.state_forwards('hackathon', state)
    return state.apps


@pytest.fixture
def backfill():
    apps = thread_state_apps()
    ticket_model = apps.get_model('hackathon', 'SupportTicket')
    reply = ticket_model._meta.get_field('reply')
    # 测试库已经跑完 0016，reply 列不在了；临时加回来，给默认值以免表里有行时加不上，
    # 整个改动随本用例的事务回滚。
    reply.default = ''
    with connection.schema_editor() as editor:
        editor.add_field(ticket_model, reply)
    module = importlib.import_module(f'eventyay.hackathon.migrations.{MIGRATION}')
    return module.move_replies_into_thread, apps


def make_old_ticket(apps, hackathon, user, reply, handler=None):
    return apps.get_model('hackathon', 'SupportTicket').objects.create(
        hackathon_id=hackathon.pk,
        user_id=user.pk,
        subject='上传卡在 90%',
        body='10:20 左右开始，刷新后仍然卡住。',
        category='tech',
        handler_id=handler.pk if handler else None,
        reply=reply,
    )


def test_backfill_keeps_the_handler_as_staff_and_the_ticket_time(backfill, hackathon, user, organizer_user):
    move, apps = backfill
    ticket = make_old_ticket(apps, hackathon, user, '请改用 Chrome 重试，并保留回执。', handler=organizer_user)
    move(apps, None)
    message = apps.get_model('hackathon', 'TicketMessage').objects.get(ticket_id=ticket.pk)
    assert message.author_id == organizer_user.pk
    assert message.from_staff is True
    assert message.body == '请改用 Chrome 重试，并保留回执。'
    ticket.refresh_from_db()
    assert message.created == ticket.updated


def test_backfill_without_a_handler_does_not_claim_to_be_staff(backfill, hackathon, user):
    move, apps = backfill
    ticket = make_old_ticket(apps, hackathon, user, '这条说明没有处理人。')
    move(apps, None)
    message = apps.get_model('hackathon', 'TicketMessage').objects.get(ticket_id=ticket.pk)
    assert message.author_id == user.pk
    assert message.from_staff is False


def test_backfill_skips_empty_reply_and_keeps_long_text(backfill, hackathon, user, organizer_user):
    move, apps = backfill
    empty = make_old_ticket(apps, hackathon, user, '')
    long_reply = '处' * 5000
    handled = make_old_ticket(apps, hackathon, user, long_reply, handler=organizer_user)
    move(apps, None)
    messages = apps.get_model('hackathon', 'TicketMessage').objects
    assert not messages.filter(ticket_id=empty.pk).exists()
    assert messages.get(ticket_id=handled.pk).body == long_reply
    assert messages.count() == 1
