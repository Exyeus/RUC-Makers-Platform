"""Current team progress mirrors real membership without changing its rules."""

import uuid
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.contrib.messages import get_messages
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event
from eventyay.hackathon import matching
from eventyay.hackathon.models import (
    Hackathon,
    JoinRequest,
    MatchingBlock,
    MatchIntent,
    Participant,
    Team,
    TeamProposal,
    Track,
)
from tests.hackathon.test_matching import market as market
from tests.hackathon.test_matching import player, proposal
from tests.hackathon.test_team_sections import person
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def progress(client, h):
    response = client.get(endpoint(h, 'teams'))
    assert response.status_code == 200
    return response, BeautifulSoup(response.content, 'html.parser').select_one('#team-progress')


def test_pending_application_can_be_withdrawn_from_my_team(client, hackathon, team):
    applicant = person(hackathon, 'progress-applicant')
    client.force_login(applicant.user)
    response = client.post(
        endpoint(hackathon, 'team-action'), {'action': 'apply', 'team': team.pk, 'message': '希望一起参赛'}
    )
    assert response.status_code == 302
    request = JoinRequest.objects.get(hackathon=hackathon, participant=applicant, team=team)
    response, current = progress(client, hackathon)
    assert team.name in current.text and team.captain.name in current.text
    assert '等待队长' in current.text and '还没有正式加入' in current.text
    assert not BeautifulSoup(response.content, 'html.parser').select('.team-empty-state')
    create = current.select_one('a[href="' + endpoint(hackathon, 'team-edit') + '"]')
    assert create and client.get(create['href']).status_code == 200
    form = current.select_one('form')
    assert form['action'] == endpoint(hackathon, 'team-action')
    data = {field['name']: field.get('value', '') for field in form.select('input[name]')}
    assert data['action'] == 'withdraw' and data['application'] == str(request.pk)
    assert client.post(form['action'], data).status_code == 302
    request.refresh_from_db()
    applicant.refresh_from_db()
    assert request.state == 'withdrawn' and applicant.team_id is None
    assert progress(client, hackathon)[1] is None


def test_captain_approval_replaces_pending_progress_with_real_membership(client, hackathon, team):
    applicant = person(hackathon, 'progress-approved')
    request = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='想加入')
    client.force_login(team.captain.user)
    assert (
        client.post(endpoint(hackathon, 'team-action'), {'action': 'accept', 'application': request.pk}).status_code
        == 302
    )
    client.force_login(applicant.user)
    response, current = progress(client, hackathon)
    applicant.refresh_from_db()
    assert current is None and applicant.team_id == team.pk
    own = BeautifulSoup(response.content, 'html.parser').select_one('.own-team')
    assert team.name in own.text and '已正式入队' in own.text
    assert '等待队长' not in own.text


def test_progress_excludes_other_applicants_events_and_terminal_requests(client, hackathon, team):
    applicant = person(hackathon, 'progress-owner')
    stranger = person(hackathon, 'progress-stranger')
    owned = JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='本人的待办')
    JoinRequest.objects.create(hackathon=hackathon, participant=stranger, team=team, message='他人的私密申请')
    for state in ('accepted', 'withdrawn', 'declined', 'unavailable'):
        other = Team.objects.create(hackathon=hackathon, captain=team.captain, track=team.track, name=f'结束-{state}')
        JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=other, state=state, message=state)
    foreign_event = Event.objects.create(
        organizer=hackathon.event.organizer,
        name='Another Hackathon',
        slug='other-progress',
        date_from=hackathon.event.date_from,
        date_to=hackathon.event.date_to,
        timezone=hackathon.event.timezone,
        currency=hackathon.event.currency,
    )
    foreign = Hackathon.objects.create(
        event=foreign_event,
        slug='other-progress',
        name='另届活动',
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    with scope(event=foreign_event):
        foreign_owner = Participant.objects.create(
            hackathon=foreign, user=applicant.user, name='progress-foreign-owner', consent=True
        )
        foreign_track = Track.objects.create(hackathon=foreign, name='另一届赛道', state='published')
        foreign_team = Team.objects.create(
            hackathon=foreign, captain=foreign_owner, track=foreign_track, name='另一届的队伍'
        )
        JoinRequest.objects.create(hackathon=foreign, participant=foreign_owner, team=foreign_team, message='其他活动')
    client.force_login(applicant.user)
    response, current = progress(client, hackathon)
    assert list(response.context['pending_applications']) == [owned]
    assert current and '另一届的队伍' not in current.text and '结束-' not in current.text
    assert '他人的私密申请' not in response.content.decode()


def test_frozen_application_remains_visible_without_an_unavailable_withdraw_action(client, hackathon, team):
    applicant = person(hackathon, 'progress-frozen')
    JoinRequest.objects.create(hackathon=hackathon, participant=applicant, team=team, message='冻结前申请')
    hackathon.team_freeze_at = timezone.now() - timedelta(seconds=1)
    hackathon.save(update_fields=['team_freeze_at'])
    client.force_login(applicant.user)
    _, current = progress(client, hackathon)
    assert team.name in current.text and '名单已冻结' in current.text
    assert not current.select('form')
    assert not current.select('a[href="' + endpoint(hackathon, 'team-edit') + '"]')
    assert current.select_one('a[href="' + endpoint(hackathon, 'support') + '"]')


def test_accepted_exchange_is_visible_without_becoming_membership_and_honors_blocks(client, market):
    market.matching_roster_enabled = False
    market.save(update_fields=['matching_roster_enabled'])
    viewer, partner, blocked, stranger = [player(market, index) for index in range(4)]
    accepted = []
    for sender, recipient in ((viewer, partner), (blocked, viewer), (partner, stranger)):
        accepted.append(
            MatchIntent.objects.create(
                hackathon=market,
                sender=sender,
                recipient=recipient,
                state='accepted',
                message='不在进度摘要展示的交流正文',
                expires_at=timezone.now() + timedelta(days=1),
            )
        )
    for state in ('pending', 'cancelled', 'declined', 'expired'):
        MatchIntent.objects.create(
            hackathon=market,
            sender=viewer,
            recipient=stranger,
            state=state,
            message='其他状态',
            expires_at=timezone.now() + timedelta(days=1),
        )
    MatchingBlock.objects.create(hackathon=market, source=blocked, target=viewer)
    client.force_login(viewer.user)
    response, current = progress(client, market)
    assert response.context['accepted_conversations'] == [{'id': accepted[0].pk, 'name': partner.name}]
    assert '已同意交流，尚未入队' in current.text and partner.name in current.text
    assert '不在进度摘要展示的交流正文' not in current.text
    assert current.select_one('a[href="' + endpoint(market, 'social-chat', intent_id=accepted[0].pk) + '"]')
    assert not response.context['own_team'] and not viewer.team_id
    assert partner.contact not in response.content.decode()
    create = current.select_one('a[href="' + endpoint(market, 'team-edit') + '"]')
    assert create and client.get(create['href']).status_code == 200
    market.team_freeze_at = timezone.now() - timedelta(seconds=1)
    market.save(update_fields=['team_freeze_at'])
    _, frozen = progress(client, market)
    assert frozen and not frozen.select('a[href="' + endpoint(market, 'team-edit') + '"]')


@pytest.mark.parametrize('roster', [False, True])
def test_acceptance_message_describes_the_enabled_roster_path(client, market, roster):
    market.matching_roster_enabled = roster
    market.save(update_fields=['matching_roster_enabled'])
    sender, recipient = player(market, 0), player(market, 1)
    result = matching.send_intent(market, sender.user, recipient.pk, '先交流合作方向')
    client.force_login(recipient.user)
    response = client.post(
        endpoint(market, name='matching'),
        {
            'operation': 'intent-decision',
            'intent_id': result['intent_id'],
            'decision': 'accept',
            'version': 1,
            'idempotency_key': uuid.uuid4().hex,
        },
    )
    assert response.status_code == 302
    text = ' '.join(str(message) for message in get_messages(response.wsgi_request))
    assert '已同意交流' in text and '这不会变更正式队伍' in text
    if roster:
        assert '名单提案' in text and '完整名单需每位成员同意' in text
    else:
        assert '申请入队，由队长处理' in text and '全员确认' not in text and '名单提案' not in text
    recipient.refresh_from_db()
    assert recipient.team_id is None
    assert progress(client, market)[1].select_one('#team-progress-heading')


def test_formal_roster_progress_uses_current_proposals_and_real_membership(client, market):
    first, second = player(market, 0), player(market, 1)
    result = proposal(market, [first, second])
    client.force_login(first.user)
    response, current = progress(client, market)
    assert len(response.context['awaiting_proposals']) == 1
    assert '有待确认的名单提案' in current.text
    assert current.select_one('a[href="' + endpoint(market, name='matching') + '#progress"]')
    assert not current.select('a[href="' + endpoint(market, 'team-edit') + '"]')
    assert not current.select('form[action="' + endpoint(market, name='team-action') + '"]')
    TeamProposal.objects.filter(pk=result['proposal_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert progress(client, market)[1] is None


def test_expired_intent_does_not_report_a_successful_acceptance(client, market):
    sender, recipient = player(market, 0), player(market, 1)
    item = MatchIntent.objects.create(
        hackathon=market,
        sender=sender,
        recipient=recipient,
        message='已过期意向',
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    client.force_login(recipient.user)
    response = client.post(
        endpoint(market, name='matching'),
        {
            'operation': 'intent-decision',
            'intent_id': item.pk,
            'decision': 'accept',
            'version': item.version,
            'idempotency_key': uuid.uuid4().hex,
        },
    )
    assert response.status_code == 302
    text = ' '.join(str(message) for message in get_messages(response.wsgi_request))
    assert '合作意向已过期' in text and '已同意交流' not in text
    item.refresh_from_db()
    assert item.state == 'expired' and progress(client, market)[1] is None
