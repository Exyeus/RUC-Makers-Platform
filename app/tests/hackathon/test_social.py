import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from eventyay.hackathon import matching, social
from eventyay.hackathon.models import (
    CommunityPost,
    CommunityResponse,
    ContactGrant,
    MatchIntent,
    PostReaction,
    SocialPreference,
    SocialReport,
)
from tests.hackathon.test_matching import market as market
from tests.hackathon.test_matching import player


def accepted(h, a, b):
    return MatchIntent.objects.create(
        hackathon=h,
        sender=a,
        recipient=b,
        message='hello',
        state='accepted',
        expires_at=timezone.now() + timedelta(days=1),
    )


@pytest.mark.django_db
def test_chat_consent_membership_block_and_preferences(market):
    a, b, c = [player(market, i) for i in range(3)]
    item = accepted(market, a, b)
    assert social.chat_intent(market, a, item.pk)[1].pk == b.pk
    with pytest.raises(PermissionDenied):
        social.chat_intent(market, c, item.pk)
    item.state = 'pending'
    item.save()
    with pytest.raises(PermissionDenied):
        social.chat_intent(market, a, item.pk)
    item.state = 'accepted'
    item.save()
    SocialPreference.objects.create(hackathon=market, participant=b, allow_chat=False)
    with pytest.raises(PermissionDenied):
        social.chat_intent(market, a, item.pk)
    SocialPreference.objects.filter(participant=b).update(allow_chat=True)
    matching.set_block(market, b.user, a.pk)
    with pytest.raises(PermissionDenied):
        social.chat_intent(market, a, item.pk)


@pytest.mark.django_db
def test_contact_grants_isolated_and_revocable(market):
    a, b, c = [player(market, i) for i in range(3)]
    ab, bc = accepted(market, a, b), accepted(market, b, c)
    SocialPreference.objects.create(
        hackathon=market, participant=b, contacts={'wechat': 'secret-wechat', 'phone': 'secret-phone'}
    )
    assert social.contacts(market, ab, b) == {}
    grant = ContactGrant.objects.create(hackathon=market, intent=ab, owner=b, fields=['wechat'])
    assert social.contacts(market, ab, b) == {'wechat': 'secret-wechat'}
    assert social.contacts(market, bc, b) == {}
    grant.fields = []
    grant.save()
    assert matching.shared_contacts(market, a, ab) == {}


@pytest.mark.django_db
def test_message_retry_and_rate_limit(market):
    a, b = [player(market, i) for i in range(2)]
    item = accepted(market, a, b)
    key = uuid.uuid4()
    first = social.send(market, a, item, ' hello ', key)
    assert social.send(market, a, item, ' hello ', key).pk == first.pk
    with pytest.raises(ValidationError):
        social.send(market, a, item, 'different', key)
    for i in range(19):
        social.send(market, a, item, str(i), uuid.uuid4())
    with pytest.raises(ValidationError):
        social.send(market, a, item, 'too many', uuid.uuid4())


@pytest.mark.django_db
def test_poll_escape_and_post_endpoints(market, client):
    a, b, c = [player(market, i) for i in range(3)]
    item = accepted(market, a, b)
    url = reverse('hackathon:social-chat', args=[market.slug, item.pk])
    client.force_login(a.user)
    response = client.post(
        url, {'body': '<script>bad()</script>', 'client_id': str(uuid.uuid4())}, HTTP_X_REQUESTED_WITH='fetch'
    )
    assert response.status_code == 200
    assert b'&lt;script&gt;' in client.get(url).content
    assert 'no-store' in client.get(url, {'poll': '1'})['Cache-Control']
    client.force_login(c.user)
    assert client.get(url, {'poll': '1'}).status_code == 403


@pytest.mark.django_db
def test_comments_private_reply_withdraw_like_and_report(market, client):
    a, b, c = [player(market, i) for i in range(3)]
    post = CommunityPost.objects.create(
        hackathon=market, author=a, title='作品', body='一起做', kind='idea', is_public=True, is_open=True
    )
    url = reverse('hackathon:post-detail', args=[market.slug, post.pk])
    private = CommunityResponse.objects.create(
        hackathon=market, post=post, author=b, body='private-secret', private=True
    )
    public = CommunityResponse.objects.create(hackathon=market, post=post, author=b, body='public-text', private=False)
    client.force_login(c.user)
    assert b'private-secret' not in client.get(url).content
    assert client.post(url, {'body': 'reply', 'parent_id': private.pk}).status_code == 404
    assert client.post(url, {'body': 'reply', 'parent_id': public.pk}).status_code == 302
    for _ in range(2):
        client.post(url, {'action': 'like'})
    assert PostReaction.objects.filter(post=post, author=c).count() == 1
    assert client.post(url, {'action': 'remove', 'response_id': public.pk}).status_code == 403
    client.post(url, {'action': 'report', 'response_id': public.pk, 'reason': '骚扰'})
    assert SocialReport.objects.filter(reporter=c).count() == 1
    client.force_login(b.user)
    client.post(url, {'action': 'remove', 'response_id': public.pk})
    public.refresh_from_db()
    assert public.removed and public.body == ''


@pytest.mark.django_db
def test_hidden_fields_and_request_optout(market, client):
    a, b = [player(market, i) for i in range(2)]
    b.bio = 'SECRET'
    b.save()
    SocialPreference.objects.create(hackathon=market, participant=b, hidden_fields=['bio'], allow_requests=False)
    assert matching.public_person(b)['bio'] == ''
    with pytest.raises(matching.MatchingError):
        matching.send_intent(market, a.user, b.pk, 'hi')
    client.force_login(a.user)
    response = client.get(reverse('hackathon:teams', args=[market.slug]), {'q': 'SECRET'})
    assert b'SECRET' not in response.content.replace(b'value="SECRET"', b'')


@pytest.mark.django_db
def test_blocked_conversation_can_report_and_revoke_but_not_read(market, client):
    a, b = [player(market, i) for i in range(2)]
    item = accepted(market, a, b)
    grant = ContactGrant.objects.create(hackathon=market, intent=item, owner=a, fields=['phone'])
    matching.set_block(market, a.user, b.pk)
    client.force_login(a.user)
    url = reverse('hackathon:social-chat', args=[market.slug, item.pk])
    assert client.get(url).status_code == 200
    assert client.get(url, {'poll': '1'}).status_code == 403
    assert client.post(url, {'body': 'no', 'client_id': str(uuid.uuid4())}).status_code == 403
    assert client.post(url, {'action': 'share'}).status_code == 302
    grant.refresh_from_db()
    assert grant.fields == []
    assert client.post(url, {'action': 'report', 'reason': '骚扰'}).status_code == 302
    assert SocialReport.objects.filter(reporter=a).exists()
