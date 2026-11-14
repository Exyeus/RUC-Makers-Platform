"""Event-scoped communication boundaries; callers serialize writes with event lock."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from eventyay.hackathon.models import ChatMessage, ContactGrant, MatchingContact, MatchIntent, SocialPreference


PUBLIC_FIELDS = ('tagline', 'bio', 'skills', 'keywords', 'school')
CONTACT_FIELDS = ('wechat', 'phone', 'email', 'legacy')


def preference(p):
    return SocialPreference.objects.filter(hackathon_id=p.hackathon_id, participant=p).first()


def hidden(p):
    pref = preference(p)
    return set(pref.hidden_fields) if pref else set()


def mask(p):
    for key in hidden(p) & set(PUBLIC_FIELDS):
        setattr(p, key, [] if key == 'keywords' else '')
    return p


def rate(query, count, seconds):
    if query.filter(created__gte=timezone.now() - timedelta(seconds=seconds)).count() >= count:
        raise ValidationError('操作过于频繁，请稍后再试。')


def chat_intent(h, p, intent_id):
    from eventyay.hackathon import identity, matching

    item = (
        MatchIntent.objects.filter(hackathon=h, pk=intent_id, state='accepted')
        .select_related('sender__user', 'recipient__user')
        .first()
    )
    if not item or p.pk not in {item.sender_id, item.recipient_id}:
        raise PermissionDenied
    other = item.recipient if p.pk == item.sender_id else item.sender
    if matching.blocked(p, other) or not matching.visible_to(p, other) or not matching.visible_to(other, p):
        raise PermissionDenied
    for member in (p, other):
        pref = preference(member)
        if (pref and not pref.allow_chat) or not identity.admitted(member.user, h):
            raise PermissionDenied
    return item, other


def send(h, p, item, body, client_id):
    body = body.strip()
    old = ChatMessage.objects.filter(hackathon=h, intent=item, author=p, client_id=client_id).first()
    if old:
        if old.body != body:
            raise ValidationError('重复编号对应不同消息，请重新发送。')
        return old
    body = body.strip()
    if not body or len(body) > 2000:
        raise ValidationError('消息须为1–2000字。')
    rate(ChatMessage.objects.filter(hackathon=h, author=p), 20, 60)
    rate(ChatMessage.objects.filter(hackathon=h, author=p), 300, 86400)
    return ChatMessage.objects.create(hackathon=h, intent=item, author=p, body=body, client_id=client_id)


def contacts(h, item, owner):
    grant = ContactGrant.objects.filter(hackathon=h, intent=item, owner=owner).first()
    pref = preference(owner)
    values = dict(pref.contacts) if pref else {}
    legacy = MatchingContact.objects.filter(hackathon=h, participant=owner, share_on_accept=True).first()
    if legacy:
        values['legacy'] = legacy.text
    return {k: v for k, v in values.items() if grant and k in grant.fields and k in CONTACT_FIELDS and v}
