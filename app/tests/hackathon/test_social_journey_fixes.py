import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from eventyay.hackathon.models import (
    AuditEntry,
    ChatMessage,
    CommunityPost,
    CommunityResponse,
    MatchIntent,
    SocialReport,
)
from tests.hackathon.test_matching import market as market
from tests.hackathon.test_matching import player


pytestmark = pytest.mark.django_db


def accepted(h, a, b):
    return MatchIntent.objects.create(
        hackathon=h,
        sender=a,
        recipient=b,
        message='一起讨论作品',
        state='accepted',
        expires_at=timezone.now() + timedelta(days=1),
    )


def location(h, name, **kwargs):
    return reverse('hackathon:' + name, kwargs={'hackathon_slug': h.slug, **kwargs})


def make_post(h, author):
    return CommunityPost.objects.create(
        hackathon=h, author=author, title='交流卡片', body='公开作品', kind='idea', is_public=True, is_open=True
    )


def test_organizer_without_player_can_remove_private_comment(client, market, organizer_user):
    author, respondent = player(market, 1), player(market, 2)
    post = make_post(market, author)
    comment = CommunityResponse.objects.create(
        hackathon=market, post=post, author=respondent, body='待处置的私密回应', private=True
    )
    client.force_login(organizer_user)
    response = client.post(
        location(market, 'post-detail', post_id=post.pk), {'action': 'remove', 'response_id': comment.pk}
    )
    assert response.status_code == 302
    assert response['Location'] == location(market, 'post-detail', post_id=post.pk)
    comment.refresh_from_db()
    assert comment.removed and not comment.body
    assert AuditEntry.objects.filter(hackathon=market, actor=organizer_user, action='撤回交流回应').exists()


def test_ordinary_player_cannot_use_moderation_bypass(client, market):
    author, respondent, outsider = [player(market, i) for i in range(3)]
    post = make_post(market, author)
    comment = CommunityResponse.objects.create(
        hackathon=market, post=post, author=respondent, body='保留', private=False
    )
    client.force_login(outsider.user)
    assert (
        client.post(
            location(market, 'post-detail', post_id=post.pk), {'action': 'remove', 'response_id': comment.pk}
        ).status_code
        == 403
    )
    comment.refresh_from_db()
    assert comment.body == '保留' and not comment.removed


def test_history_pages_and_poll_cover_all_messages_without_activity_lock(client, market):
    a, b = player(market, 0), player(market, 1)
    item = accepted(market, a, b)
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(hackathon=market, intent=item, author=a, body=f'历史-{i:03}', client_id=uuid.uuid4())
            for i in range(205)
        ]
    )
    client.force_login(a.user)
    path = location(market, 'social-chat', intent_id=item.pk)
    # A polling read must not serialize unrelated uploads/roster writes on the event row.
    with patch(
        'eventyay.hackathon.social_views.services.lock_event', side_effect=AssertionError('GET took write lock')
    ):
        with CaptureQueriesContext(connection) as queries:
            newest = client.get(path)
        assert newest.status_code == 200
        assert not any('FOR UPDATE' in query['sql'].upper() for query in queries)
        assert len(newest.context['chat_messages']) == 100
        assert '历史-204' in newest.content.decode() and '历史-000' not in newest.content.decode()
        older = client.get(path, {'before': newest.context['oldest_id']})
        oldest = client.get(path, {'before': older.context['oldest_id']})
        ids = [message.pk for response in (newest, older, oldest) for message in response.context['chat_messages']]
        assert len(ids) == len(set(ids)) == 205
        assert '历史-000' in oldest.content.decode()
        assert not oldest.context['has_older']
        fresh = ChatMessage.objects.create(
            hackathon=market, intent=item, author=b, body='历史页打开后新消息', client_id=uuid.uuid4()
        )
        data = client.get(path, {'poll': '1', 'after': older.context['poll_after']}).json()
        assert [row['id'] for row in data['messages']] == [fresh.pk]
        first = client.get(path, {'poll': '1', 'after': 0}).json()
        assert len(first['messages']) == 100 and first['has_more']
        second = client.get(path, {'poll': '1', 'after': first['messages'][-1]['id']}).json()
        assert len(second['messages']) == 100 and second['has_more']
        last = client.get(path, {'poll': '1', 'after': second['messages'][-1]['id']}).json()
        assert len(last['messages']) == 6 and not last['has_more']


def test_history_and_poll_remain_inaccessible_after_chat_withdrawal(client, market):
    a, b, stranger = [player(market, i) for i in range(3)]
    item = accepted(market, a, b)
    message = ChatMessage.objects.create(
        hackathon=market, intent=item, author=a, body='不再展示的历史', client_id=uuid.uuid4()
    )
    path = location(market, 'social-chat', intent_id=item.pk)
    client.force_login(stranger.user)
    assert client.get(path, {'before': message.pk + 1}).status_code == 403
    client.force_login(a.user)
    item.state = 'cancelled'
    item.save()
    assert '不再展示的历史' not in client.get(path, {'before': message.pk + 1}).content.decode()
    assert client.get(path, {'poll': '1'}).status_code == 403


def test_send_acknowledgement_provides_next_id_and_keeps_retry_idempotent(client, market):
    a, b = player(market, 0), player(market, 1)
    item = accepted(market, a, b)
    path = location(market, 'social-chat', intent_id=item.pk)
    client.force_login(a.user)
    retry_id = str(uuid.uuid4())
    payload = {'body': '同一条重试', 'client_id': retry_id}
    first = client.post(path, payload, HTTP_X_REQUESTED_WITH='fetch')
    retry = client.post(path, payload, HTTP_X_REQUESTED_WITH='fetch')
    assert first.status_code == retry.status_code == 200
    assert str(uuid.UUID(first.json()['next_client_id'])) != retry_id
    assert ChatMessage.objects.filter(intent=item).count() == 1
    assert (
        client.post(
            path,
            {'body': '下一条消息', 'client_id': first.json()['next_client_id']},
            HTTP_X_REQUESTED_WITH='fetch',
        ).status_code
        == 200
    )
    assert ChatMessage.objects.filter(intent=item).count() == 2


def test_report_evidence_is_targeted_cut_off_and_audited(client, market, organizer_user):
    a, b, c = [player(market, i) for i in range(3)]
    ab, ac = accepted(market, a, b), accepted(market, a, c)
    ChatMessage.objects.create(hackathon=market, intent=ab, author=b, body='已举报上下文', client_id=uuid.uuid4())
    report = SocialReport.objects.create(
        hackathon=market, reporter=a, target_type='chat', target_id=str(ab.pk), reason='请核查'
    )
    ChatMessage.objects.create(
        hackathon=market, intent=ab, author=b, body='举报后的新消息不展示', client_id=uuid.uuid4()
    )
    ChatMessage.objects.create(hackathon=market, intent=ac, author=c, body='别的私聊不展示', client_id=uuid.uuid4())
    path = location(market, 'social-reports')
    client.force_login(a.user)
    assert client.get(path, {'report_id': report.pk}).status_code == 403
    client.force_login(organizer_user)
    selected = client.get(path, {'report_id': report.pk})
    assert selected.status_code == 200
    html = selected.content.decode()
    assert '已举报上下文' in html
    assert '举报后的新消息不展示' not in html and '别的私聊不展示' not in html
    assert AuditEntry.objects.filter(hackathon=market, action='查看沟通举报证据', target=str(report.pk)).exists()
    assert client.post(path, {'report_id': report.pk}).status_code == 302
    report.refresh_from_db()
    assert not report.resolved
    assert client.post(path, {'report_id': report.pk, 'resolution': '已核查并联系双方处理'}).status_code == 302
    report.refresh_from_db()
    assert report.resolved
    assert AuditEntry.objects.filter(hackathon=market, action='处理沟通举报', detail='已核查并联系双方处理').exists()


def test_comment_report_does_not_reveal_other_private_responses(client, market, organizer_user):
    a, b, c = [player(market, i) for i in range(3)]
    post = make_post(market, a)
    target = CommunityResponse.objects.create(
        hackathon=market, post=post, author=b, body='被举报私密回应', private=True
    )
    CommunityResponse.objects.create(hackathon=market, post=post, author=c, body='无关私密回应', private=True)
    report = SocialReport.objects.create(
        hackathon=market, reporter=a, target_type='comment', target_id=str(target.pk), reason='核查回应'
    )
    client.force_login(organizer_user)
    response = client.get(location(market, 'social-reports'), {'report_id': report.pk})
    assert response.status_code == 200
    assert '被举报私密回应' in response.content.decode()
    assert '无关私密回应' not in response.content.decode()
    assert f'#response-{target.pk}' in response.content.decode()
