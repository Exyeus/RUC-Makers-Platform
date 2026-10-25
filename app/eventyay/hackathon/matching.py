"""组队广场services. Callers enter the event scope; event locks serialize roster writes."""

import hashlib
import json
from datetime import timedelta
from itertools import combinations
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from eventyay.hackathon import match_core as core
from eventyay.hackathon.matching_forms import ROLE_LABELS
from eventyay.hackathon.models import (
    Idea,
    IdeaImplementation,
    MatchingBlock,
    MatchingContact,
    MatchingFeedback,
    MatchingOutbox,
    MatchingProcessedEvent,
    MatchingProfile,
    MatchingReceipt,
    MatchIntent,
    Participant,
    ProposalConsent,
    RecommendationRun,
    Team,
    TeamMembership,
    TeamProposal,
    Track,
)
from eventyay.hackathon.permissions import is_organizer
from eventyay.hackathon.services import audit, lock_event


ALGORITHM_VERSION = f'idea-market-1.0/{core.ALGORITHM_VERSION}'


class MatchingError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    ).hexdigest()


def db_now():
    with connection.cursor() as cursor:
        cursor.execute('SELECT clock_timestamp()')
        return cursor.fetchone()[0]


def require_enabled(h):
    if not h.matching_enabled:
        raise MatchingError('MATCHING_DISABLED', '组队广场尚未开放。', 403)


def require_open(h):
    require_enabled(h)
    if not h.matching_roster_enabled:
        raise MatchingError('POLICY_NOT_APPROVED', '正式名单政策尚未批准。', 403)
    if h.matching_frozen_at or db_now() >= h.team_freeze_at:
        raise MatchingError('ROSTER_FROZEN', '队伍名单已冻结。')


def emit(h, kind, payload):
    return MatchingOutbox.objects.create(hackathon=h, kind=kind, payload=payload)


def person(h, pk):
    try:
        return Participant.objects.get(hackathon=h, pk=pk)
    except (Participant.DoesNotExist, ValueError, TypeError) as error:
        raise MatchingError('NOT_AVAILABLE', '对象不可用。', 404) from error


def own_person(h, user):
    try:
        return Participant.objects.get(hackathon=h, user=user)
    except Participant.DoesNotExist as error:
        raise MatchingError('REGISTRATION_REQUIRED', '请先完成本活动报名。', 403) from error


def profile_for(p):
    return MatchingProfile.objects.filter(participant=p, hackathon_id=p.hackathon_id).first()


def blocked(a, b) -> bool:
    return (
        MatchingBlock.objects.filter(hackathon_id=a.hackathon_id)
        .filter(Q(source=a, target=b) | Q(source=b, target=a))
        .exists()
    )


def visible_to(viewer, target, *, discovery=False) -> bool:
    profile = profile_for(target)
    return bool(
        profile
        and profile.opt_in
        and profile.visibility != 'paused'
        and (not discovery or profile.visibility == 'event')
        and viewer.hackathon_id == target.hackathon_id
        and not blocked(viewer, target)
    )


def public_person(p, profile=None) -> dict:
    profile = profile if profile is not None else profile_for(p)
    return {
        'id': str(p.pk),
        'name': p.name,
        'tagline': p.tagline,
        'bio': p.bio,
        'skills': profile.skills if profile else {},
        'interests': profile.interests if profile else [],
        'version': profile.version if profile else 0,
        'updated_at': profile.updated.isoformat() if profile else None,
        'team_id': str(p.team_id) if p.team_id else None,
    }


@transaction.atomic
def execute(h, user, operation: str, key: str, body: dict, callback):
    h = lock_event(h)
    if operation != 'configure':
        require_enabled(h)
    if not key or len(key) > 100:
        raise MatchingError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效 Idempotency-Key。', 422)
    body_hash = digest(body)
    old = MatchingReceipt.objects.filter(hackathon=h, actor=user, operation=operation, key=key).first()
    if old:
        if old.body_hash != body_hash:
            raise MatchingError('IDEMPOTENCY_BODY_MISMATCH', '相同请求编号不能用于不同内容。')
        return old
    # An inner savepoint lets us persist failure receipts without partial domain writes.
    try:
        with transaction.atomic():
            result = callback(h)
        status = (410 if result.get('error') == 'PROPOSAL_EXPIRED' else 409) if 'error' in result else 200
    except MatchingError as error:
        result, status = {'error': error.code, 'message': error.message}, error.status
    return MatchingReceipt.objects.create(
        hackathon=h,
        actor=user,
        operation=operation,
        key=key,
        body_hash=body_hash,
        result=result,
        status=status,
    )


def save_profile(h, actor, data):
    p = own_person(h, actor)
    current = profile_for(p)
    if data['version'] != (current.version if current else 0):
        raise MatchingError('STALE_PROFILE', '资料已更新，请刷新后再编辑。')
    values = {
        key: data[key]
        for key in (
            'skills',
            'interests',
            'learning_roles',
            'slots',
            'goal',
            'async_ok',
            'visibility',
            'opt_in',
            'batch_opt_in',
            'help_requested',
        )
    }
    values.update(needs={role: 1 for role in data['needs']}, version=data['version'] + 1, confirmed_at=timezone.now())
    profile, _ = MatchingProfile.objects.update_or_create(hackathon=h, participant=p, defaults=values)
    if data.get('availability_confirmed') and 'unavailable_slots' in data:
        p.unavailable_slots = data['unavailable_slots']
        p.save(update_fields=['unavailable_slots', 'updated'])
    MatchingContact.objects.update_or_create(
        hackathon=h, participant=p, defaults={'text': data['contact'], 'share_on_accept': data['share_on_accept']}
    )
    RecommendationRun.objects.filter(hackathon=h).delete()
    emit(h, 'MatchingProfileChanged', {'participant_id': str(p.pk), 'version': profile.version})
    return {'version': profile.version}


def save_idea(h, actor, form, idea_id=None):
    p = own_person(h, actor)
    if not h.matching_idea_min_chars:
        raise MatchingError('IDEA_POLICY_NOT_APPROVED', '请运营先配置原方案的 Idea 字数要求。', 403)
    if any(
        len(form.cleaned_data[field].strip()) < h.matching_idea_min_chars
        for field in ('problem', 'audience', 'solution')
    ):
        raise MatchingError('IDEA_TOO_SHORT', f'Idea 三要素各至少 {h.matching_idea_min_chars} 字。', 422)
    current = Idea.objects.filter(hackathon=h, pk=idea_id).first() if idea_id else None
    if idea_id and (not current or current.author_id != p.pk):
        raise PermissionDenied
    if form.cleaned_data['version'] != (current.version if current else 0):
        raise MatchingError('STALE_IDEA', 'Idea 已更新，请刷新。')
    item = form.save(commit=False)
    item.hackathon, item.author = h, p
    item.interests = form.cleaned_data['interest_tags']
    item.role_weights = {role: 1 for role in form.cleaned_data['roles']}
    item.version = (current.version if current else 0) + 1
    item.save()
    emit(h, 'IdeaChanged', {'idea_id': str(item.pk), 'version': item.version})
    return {'idea_id': str(item.pk), 'version': item.version}


def send_intent(h, actor, recipient_id, message, team_id=None):
    sender, recipient = own_person(h, actor), person(h, recipient_id)
    if sender.pk == recipient.pk or not visible_to(sender, recipient):
        raise MatchingError('NOT_AVAILABLE', '该合作对象暂不可联系。', 404)
    if not message.strip() or len(message) > 500:
        raise MatchingError('INVALID_MESSAGE', '请填写 1–500 字合作意向。', 422)
    if team_id:
        team = Team.objects.filter(hackathon=h, pk=team_id, recruiting=True, captain=recipient).first()
        if not team or team.state != 'recruiting' or team.members.count() >= h.team_size:
            raise MatchingError('NOT_AVAILABLE', '队伍暂不可联系。', 404)
    now = timezone.now()
    if (
        MatchIntent.objects.filter(hackathon=h, sender=sender, state='pending', expires_at__gt=now).count()
        >= h.matching_intent_limit
    ):
        raise MatchingError('INTENT_LIMIT', '待回复意向数量已达上限。', 429)
    if MatchIntent.objects.filter(
        hackathon=h, sender=sender, recipient=recipient, created__gte=now - timedelta(hours=h.matching_cooldown_hours)
    ).exists():
        raise MatchingError('CONTACT_COOLDOWN', '请给对方一些回应时间。', 429)
    intent = MatchIntent.objects.create(
        hackathon=h,
        sender=sender,
        recipient=recipient,
        team_id=team_id,
        message=message.strip(),
        expires_at=now + timedelta(hours=h.matching_expiry_hours),
    )
    emit(h, 'MatchIntentCreated', {'intent_id': str(intent.pk), 'recipient_id': str(recipient.pk)})
    return {'intent_id': str(intent.pk), 'state': intent.state}


def decide_intent(h, actor, intent_id, decision, version):
    p = own_person(h, actor)
    item = MatchIntent.objects.filter(hackathon=h, pk=intent_id).first()
    if not item or p.pk not in {item.sender_id, item.recipient_id}:
        raise PermissionDenied
    if item.state != 'pending' or version != item.version:
        raise MatchingError('STALE_INTENT', '意向已处理，请刷新。')
    if item.expires_at <= timezone.now():
        item.state = 'expired'
    elif p.pk == item.sender_id and decision == 'cancel':
        item.state = 'cancelled'
    elif p.pk == item.recipient_id and decision in {'accept', 'decline'}:
        if decision == 'accept' and blocked(item.sender, item.recipient):
            raise MatchingError('NOT_AVAILABLE', '该合作对象暂不可联系。', 404)
        item.state = 'accepted' if decision == 'accept' else 'declined'
    else:
        raise PermissionDenied
    item.version += 1
    item.save(update_fields=['state', 'version', 'updated'])
    emit(h, 'MatchIntentDecided', {'intent_id': str(item.pk), 'state': item.state})
    return {'intent_id': str(item.pk), 'state': item.state, 'version': item.version}


def shared_contacts(h, viewer, intent):
    if viewer.pk not in {intent.sender_id, intent.recipient_id} or intent.state != 'accepted':
        return {}
    if blocked(intent.sender, intent.recipient):
        return {}
    return {
        str(c.participant_id): c.text
        for c in MatchingContact.objects.filter(
            hackathon=h, participant_id__in=[intent.sender_id, intent.recipient_id], share_on_accept=True
        )
    }


def snapshot_roster(h, members, idea, team):
    profiles = {p.participant_id: p for p in MatchingProfile.objects.filter(hackathon=h, participant__in=members)}
    if len(profiles) != len(members) or any(
        not profiles[p.pk].opt_in or profiles[p.pk].visibility == 'paused' for p in members
    ):
        raise MatchingError('PROFILE_NEEDS_CONFIRMATION', '所有成员需先确认匹配资料与参与意愿。', 422)
    if any(p.admission_state != 'admitted' for p in members):
        raise MatchingError('ROSTER_NOT_AVAILABLE', '名单资格尚未满足，请本人联系工作人员。')
    if any(blocked(a, b) for a, b in combinations(members, 2)):
        raise MatchingError('ROSTER_NOT_AVAILABLE', '当前名单不可确认，请重新协商。')
    if any(p.team_id and (not team or p.team_id != team.pk) for p in members):
        raise MatchingError('ALREADY_IN_TEAM', '名单中已有成员加入其他队伍。')
    ids = {p.pk for p in members}
    if team:
        if team.state not in {'recruiting', 'ready'}:
            raise MatchingError('ROSTER_NOT_AVAILABLE', '队伍暂不可变更。')
        if not set(team.members.values_list('pk', flat=True)) <= ids:
            raise MatchingError('STALE_PROPOSAL', '必须保留并征得现有所有成员同意。')
    if not members or len(members) > h.team_size or (len(members) == 1 and not h.matching_single_allowed):
        raise MatchingError('TEAM_FULL', '名单人数不符合当前队伍政策。')
    if idea.state != 'open' or (idea.mode == 'recruit' and idea.author_id not in ids):
        raise MatchingError('IDEA_MODE_CONFLICT', '组队型 Idea 必须保留作者；Idea 需处于开放状态。')
    existing = IdeaImplementation.objects.filter(hackathon=h, idea=idea)
    if team:
        existing = existing.exclude(team=team)
    if idea.mode == 'recruit' and existing.exists():
        raise MatchingError('IDEA_ALREADY_CLAIMED', '该组队型 Idea 已有实现队伍。')
    return {
        'policy_version': h.matching_policy_version,
        'team_size': h.team_size,
        'idea_version': idea.version,
        'roster_version': team.roster_version if team else None,
        'members': {
            str(p.pk): {'profile_version': profiles[p.pk].version, 'team_id': str(p.team_id) if p.team_id else None}
            for p in sorted(members, key=lambda x: x.pk)
        },
    }


def create_proposal(h, actor, member_ids, idea_id, track_id, name, agreement, target_team_id=None):
    require_open(h)
    creator = own_person(h, actor)
    try:
        ids = [UUID(str(value)) for value in member_ids]
    except (ValueError, TypeError) as error:
        raise MatchingError('INVALID_ROSTER', '请填写完整有效的成员编号。', 422) from error
    if len(ids) != len(set(ids)) or creator.pk not in ids:
        raise MatchingError('INVALID_ROSTER', '名单必须包含本人且不能重复。', 422)
    members = list(Participant.objects.select_for_update().filter(hackathon=h, pk__in=ids).order_by('pk'))
    if len(members) != len(ids):
        raise MatchingError('NOT_AVAILABLE', '名单中有不可用对象。', 404)
    target_team_id = target_team_id or creator.team_id
    team = Team.objects.select_for_update().filter(hackathon=h, pk=target_team_id).first() if target_team_id else None
    if target_team_id and (not team or creator.team_id != team.pk):
        raise PermissionDenied
    if not name.strip() or len(name) > 80 or not agreement.strip() or len(agreement) > 2000:
        raise MatchingError('INVALID_AGREEMENT', '请填写队名及完整合作约定。', 422)
    idea = Idea.objects.filter(hackathon=h, pk=idea_id).first()
    track = Track.objects.filter(hackathon=h, pk=track_id).first()
    if not idea or not track:
        raise MatchingError('NOT_AVAILABLE', 'Idea 或赛道不可用。', 404)
    snapshot = snapshot_roster(h, members, idea, team)
    snapshot.update(
        idea_id=str(idea.pk),
        track_id=track.pk,
        team_name=name.strip(),
        agreement=agreement.strip(),
        target_team_id=str(team.pk) if team else None,
    )
    item = TeamProposal.objects.create(
        hackathon=h,
        creator=creator,
        target_team=team,
        idea=idea,
        track=track,
        team_name=name.strip(),
        agreement=agreement.strip(),
        snapshot=snapshot,
        proposal_hash=digest(snapshot),
        expires_at=timezone.now() + timedelta(hours=h.matching_expiry_hours),
    )
    emit(h, 'TeamProposalCreated', {'proposal_id': str(item.pk), 'members': list(snapshot['members'])})
    return {'proposal_id': str(item.pk), 'proposal_hash': item.proposal_hash, 'state': item.state}


def consent_proposal(h, actor, proposal_id, proposal_hash, decision):
    require_open(h)
    p = own_person(h, actor)
    item = TeamProposal.objects.filter(hackathon=h, pk=proposal_id).first()
    if not item or str(p.pk) not in item.snapshot['members']:
        raise PermissionDenied
    ids = list(item.snapshot['members'])
    members = list(Participant.objects.select_for_update().filter(hackathon=h, pk__in=ids).order_by('pk'))
    team = (
        Team.objects.select_for_update().filter(hackathon=h, pk=item.target_team_id).first()
        if item.target_team_id
        else None
    )
    item = TeamProposal.objects.select_for_update().get(pk=item.pk, hackathon=h)
    if proposal_hash != item.proposal_hash or digest(item.snapshot) != item.proposal_hash:
        raise MatchingError('STALE_PROPOSAL', '请确认当前完整名单版本。')
    if item.state != 'awaiting_consent':
        raise MatchingError('STALE_PROPOSAL', '提案已处理，请刷新。')
    if item.expires_at <= db_now():
        item.state = 'expired'
        item.save(update_fields=['state', 'updated'])
        return {'error': 'PROPOSAL_EXPIRED', 'state': 'expired'}
    if decision not in {'accept', 'decline'}:
        raise MatchingError('INVALID_DECISION', '请选择同意或拒绝。', 422)
    try:
        current = snapshot_roster(h, members, Idea.objects.get(hackathon=h, pk=item.idea_id), team)
    except MatchingError as error:
        item.state = 'stale'
        item.save(update_fields=['state', 'updated'])
        return {'error': error.code, 'state': 'stale', 'message': error.message}
    if any(current[k] != item.snapshot[k] for k in current):
        item.state = 'stale'
        item.save(update_fields=['state', 'updated'])
        return {'error': 'STALE_PROPOSAL', 'state': 'stale'}
    if ProposalConsent.objects.filter(proposal=item, participant=p).exists():
        raise MatchingError('CONSENT_ALREADY_RECORDED', '此版本的决定已经记录。')
    ProposalConsent.objects.create(
        hackathon=h, proposal=item, participant=p, proposal_hash=proposal_hash, decision=decision
    )
    if decision == 'decline':
        item.state = 'declined'
    elif item.consents.filter(proposal_hash=proposal_hash, decision='accept').count() == len(ids):
        require_open(h)
        if Team.objects.filter(hackathon=h, name__iexact=item.team_name).exclude(pk=team.pk if team else None).exists():
            raise MatchingError('TEAM_NAME_TAKEN', '队名已被使用，请重新创建提案。')
        if not team:
            team = Team.objects.create(hackathon=h, name=item.team_name, captain=item.creator, track=item.track)
        else:
            team.name, team.track = item.team_name, item.track
        newcomers = [member for member in members if member.team_id is None]
        for member in members:
            TeamMembership.objects.get_or_create(hackathon=h, participant=member, team=team, left_at=None)
        Participant.objects.filter(hackathon=h, pk__in=ids).update(team=team, looking_for_team=False)
        team.roster_version += 1
        team.recruiting = len(members) < h.team_size
        team.state = 'recruiting' if team.recruiting else 'ready'
        team.save(update_fields=['name', 'track', 'roster_version', 'recruiting', 'state', 'updated'])
        item.target_team, item.state = team, 'confirmed'
        IdeaImplementation.objects.get_or_create(hackathon=h, idea=item.idea, team=team)
        MatchIntent.objects.filter(hackathon=h, state='pending').filter(
            Q(sender__in=newcomers) | Q(recipient__in=newcomers)
        ).update(state='cancelled')
        emit(
            h,
            'TeamMembershipConfirmed',
            {
                'proposal_id': str(item.pk),
                'team_id': str(team.pk),
                'member_ids': ids,
                'newcomer_ids': [str(n.pk) for n in newcomers],
                'policy_version': h.matching_policy_version,
            },
        )
        audit(h, actor, '全员确认正式名单', team.code, item.proposal_hash)
    item.save(update_fields=['state', 'target_team', 'updated'])
    return {
        'proposal_id': str(item.pk),
        'state': item.state,
        'team_id': str(item.target_team_id) if item.target_team_id else None,
    }


def leave(h, actor):
    require_open(h)
    p = own_person(h, actor)
    p = Participant.objects.select_for_update().get(hackathon=h, pk=p.pk)
    team = Team.objects.select_for_update().filter(hackathon=h, pk=p.team_id).first()
    if not team:
        raise MatchingError('NOT_IN_TEAM', '当前尚未入队。')
    if team.captain_id == p.pk and team.members.count() > 1:
        raise MatchingError('CAPTAIN_TRANSFER_REQUIRED', '队长需先交接队伍管理权限。')
    TeamMembership.objects.filter(hackathon=h, participant=p, left_at=None).update(left_at=timezone.now())
    p.team = None
    p.save(update_fields=['team', 'updated'])
    team.roster_version += 1
    team.state = 'dissolved' if not team.members.exists() else 'recruiting'
    team.recruiting = team.state == 'recruiting'
    team.save(update_fields=['roster_version', 'state', 'recruiting', 'updated'])
    emit(h, 'TeamMemberLeft', {'participant_id': str(p.pk), 'team_id': str(team.pk)})
    audit(h, actor, '退出正式名单', team.code)
    return {'state': 'left'}


def freeze(h, actor):
    if not is_organizer(actor, h):
        raise PermissionDenied
    if h.matching_frozen_at:
        return {'state': 'frozen', 'snapshot': h.matching_freeze_snapshot}
    h.matching_frozen_at = db_now()
    h.matching_freeze_snapshot = {
        str(t.pk): {'version': t.roster_version, 'members': sorted(str(p.pk) for p in t.members.all())}
        for t in Team.objects.filter(hackathon=h).exclude(state='dissolved').prefetch_related('members')
    }
    h.save(update_fields=['matching_frozen_at', 'matching_freeze_snapshot', 'updated'])
    Team.objects.filter(hackathon=h).exclude(state='dissolved').update(state='frozen', recruiting=False)
    TeamProposal.objects.filter(hackathon=h, state='awaiting_consent').update(state='stale')
    emit(h, 'RosterFrozen', h.matching_freeze_snapshot)
    audit(h, actor, '冻结名单', h.slug)
    return {'state': 'frozen', 'snapshot': h.matching_freeze_snapshot}


def set_block(h, actor, target_id, enabled=True):
    source, target = own_person(h, actor), person(h, target_id)
    if source.pk == target.pk:
        raise MatchingError('INVALID_TARGET', '不能屏蔽自己。', 422)
    if enabled:
        MatchingBlock.objects.get_or_create(hackathon=h, source=source, target=target)
        MatchIntent.objects.filter(hackathon=h, state__in=['pending', 'accepted']).filter(
            Q(sender=source, recipient=target) | Q(sender=target, recipient=source)
        ).update(state='cancelled')
    else:
        MatchingBlock.objects.filter(hackathon=h, source=source, target=target).delete()
    RecommendationRun.objects.filter(hackathon=h).delete()
    return {'state': 'saved'}


def consume_outbox(h, consumer='station-receipts') -> int:
    """Durable site receipts are the initial consumer. Rewards remain disabled."""
    count = 0
    with transaction.atomic():
        processed = MatchingProcessedEvent.objects.filter(hackathon=h, consumer=consumer).values('source_id')
        for item in (
            MatchingOutbox.objects.select_for_update().filter(hackathon=h).exclude(pk__in=processed).order_by('created')
        ):
            _, created = MatchingProcessedEvent.objects.get_or_create(hackathon=h, source=item, consumer=consumer)
            count += int(created)
            item.delivered_at = timezone.now()
            item.save(update_fields=['delivered_at', 'updated'])
    return count


def core_profiles(h):
    records = list(MatchingProfile.objects.filter(hackathon=h).select_related('participant').order_by('participant_id'))
    exclusions = {}
    for edge in MatchingBlock.objects.filter(hackathon=h):
        exclusions.setdefault(str(edge.source_id), set()).add(str(edge.target_id))
        exclusions.setdefault(str(edge.target_id), set()).add(str(edge.source_id))
    for a, b in MatchIntent.objects.filter(hackathon=h, state='declined').values_list('sender_id', 'recipient_id'):
        exclusions.setdefault(str(a), set()).add(str(b))
        exclusions.setdefault(str(b), set()).add(str(a))
    profiles, by_id = {}, {}
    for record in records:
        p = record.participant
        # JSON columns are checked at this boundary, including records imported outside forms.
        if (
            not isinstance(record.skills, dict)
            or not isinstance(record.needs, dict)
            or not isinstance(record.interests, list)
            or any(not isinstance(v, str) for v in record.interests)
            or (record.slots is not None and not isinstance(record.slots, list))
        ):
            continue
        try:
            profiles[str(p.pk)] = core.Profile(
                person_id=str(p.pk),
                event_id=str(h.event_id),
                skills=record.skills,
                needs=record.needs,
                interests=frozenset(record.interests) or None,
                slots=frozenset(record.slots) if record.slots is not None else None,
                goal=record.goal or None,
                eligible=p.admission_state == 'admitted',
                opt_in=record.opt_in and record.visibility != 'paused',
                blocked_ids=frozenset(exclusions.get(str(p.pk), set())),
                team_id=str(p.team_id) if p.team_id else None,
                async_ok=record.async_ok,
            )
        except (ValueError, TypeError):
            continue
        by_id[str(p.pk)] = record
    return profiles, by_id


def core_idea(idea):
    return core.Idea(
        idea_id=str(idea.pk),
        event_id=str(idea.hackathon.event_id),
        interests=frozenset(idea.interests),
        role_weights=idea.role_weights,
        owner_id=str(idea.author_id),
        require_owner=idea.mode == 'recruit',
        allow_async=idea.allow_async,
    )


def public_reasons(a, b):
    reasons = []
    shared = sorted((a.interests or frozenset()) & (b.interests or frozenset()))
    if shared:
        reasons.append({'code': 'SHARED_INTEREST', 'public_text': '共同关注：' + '、'.join(shared)})
    roles = [ROLE_LABELS.get(r, r) for r in (a.needs or {}) if (b.skills or {}).get(r, 0) > 0]
    if roles:
        reasons.append({'code': 'ROLE_CONTRIBUTION', 'public_text': '对方可贡献：' + '、'.join(sorted(roles))})
    slots = core.common_slots([a, b])
    if slots:
        reasons.append({'code': 'COMMON_TIME', 'public_text': f'已确认 {len(slots)} 个共同半小时时段，可进一步讨论。'})
    elif slots is None:
        reasons.append({'code': 'TIME_UNKNOWN', 'public_text': '协作时间尚待双方确认。'})
    return reasons


def recommendations(h, viewer, target='people', limit=5):
    require_enabled(h)
    profiles, records = core_profiles(h)
    me = profiles.get(str(viewer.pk))
    if not me or not me.opt_in:
        return {'results': [], 'unknown_fields': ['matching_profile'], 'algorithm_version': ALGORITHM_VERSION}
    # Admission is required for formal suggestions. Pending users can browse and contact.
    if not me.eligible or not h.teams_open:
        return {'results': [], 'unknown_fields': [], 'algorithm_version': ALGORITHM_VERSION}
    dismissed = {
        str(value)
        for value in MatchingFeedback.objects.filter(
            hackathon=h, participant=viewer, target_type=target, decision='dismiss'
        ).values_list('target_id', flat=True)
    }
    rows = []
    if target == 'people':
        for pid, other in profiles.items():
            if pid in dismissed or other.team_id or core.pair_reasons(me, other):
                continue
            slots = core.common_slots([me, other])
            if slots is not None and len(slots) < h.matching_min_common_slots and not (me.async_ok and other.async_ok):
                continue
            forward, reverse = core.directed_score(me, other), core.directed_score(other, me)
            score = core.harmonic(forward.value, reverse.value)
            rows.append(
                {
                    'target_id': pid,
                    'target_type': target,
                    'name': records[pid].participant.name,
                    'profile_versions': {str(viewer.pk): records[str(viewer.pk)].version, pid: records[pid].version},
                    'score': score,
                    'evidence_completeness': min(forward.evidence_weight, reverse.evidence_weight),
                    'reasons': public_reasons(me, other),
                    'unknown_fields': [k for k, v in forward.features.items() if v is None],
                    'feature_mask': {k: v is not None for k, v in forward.features.items()},
                    'available_actions': ['intent', 'save', 'dismiss'],
                    'updated_at': records[pid].updated.isoformat(),
                }
            )
    elif target == 'teams':
        if me.team_id:
            return {'results': [], 'unknown_fields': [], 'algorithm_version': ALGORITHM_VERSION}
        implementations = {
            i.team_id: i.idea
            for i in IdeaImplementation.objects.filter(hackathon=h, idea__state='open').select_related(
                'idea__hackathon'
            )
        }
        for team in Team.objects.filter(hackathon=h, recruiting=True, state='recruiting').prefetch_related('members'):
            members = list(team.members.all())
            if str(team.pk) in dismissed or len(members) >= h.team_size or not members:
                continue
            group = [profiles.get(str(p.pk)) for p in members]
            if any(p is None or core.pair_reasons(me, p) for p in group):
                continue
            full = [*group, me]
            slots = core.common_slots(full)
            idea = implementations.get(team.pk)
            async_agreed = bool(idea and idea.allow_async and all(p.async_ok for p in full))
            if slots is not None and len(slots) < h.matching_min_common_slots and not async_agreed:
                continue
            delta = core.marginal_coverage(group, me, core_idea(idea)) if idea else None
            scores = [core.reciprocal_score(me, p) for p in group]
            score = sum(scores) / len(scores) if all(s is not None for s in scores) else None
            if delta is not None and score is not None:
                score = score * 0.6 + delta * 0.4
            reasons = [
                {
                    'code': 'TEAM_VACANCY',
                    'public_text': f'当前 {len(members)} 人，还可招募 {h.team_size - len(members)} 人。',
                }
            ]
            if delta is not None and delta > 0:
                reasons.append({'code': 'MARGINAL_COVERAGE', 'public_text': '你能补充该项目当前尚缺的角色能力。'})
            if slots:
                reasons.append({'code': 'GROUP_TIME', 'public_text': f'与全队有 {len(slots)} 个共同半小时时段。'})
            rows.append(
                {
                    'target_id': str(team.pk),
                    'target_type': target,
                    'name': team.name,
                    'recipient_id': str(team.captain_id),
                    'roster_version': team.roster_version,
                    'profile_versions': {p.person_id: records[p.person_id].version for p in full},
                    'score': score,
                    'evidence_completeness': sum(core.directed_score(me, p).evidence_weight for p in group)
                    / len(group),
                    'reasons': reasons,
                    'unknown_fields': ['time'] if slots is None else [],
                    'available_actions': ['intent', 'save', 'dismiss'],
                    'updated_at': team.updated.isoformat(),
                }
            )
    else:
        raise MatchingError('INVALID_TARGET', '推荐类型无效。', 422)
    ordered = sorted(rows, key=lambda x: (x['score'] is None, -(x['score'] or 0), x['target_id']))
    exposure = {}
    for run in RecommendationRun.objects.filter(hackathon=h, viewer=viewer).order_by('-created')[:100]:
        for result in run.results:
            exposure[result['target_id']] = exposure.get(result['target_id'], 0) + 1
    chosen = ordered[: max(0, limit - 2)]
    rest = [r for r in ordered if r not in chosen]
    if rest:
        low = min(rest, key=lambda r: (exposure.get(r['target_id'], 0), r['target_id']))
        chosen.append(low)
        rest.remove(low)
    if rest:
        chosen.append(max(rest, key=lambda r: (r['updated_at'], r['target_id'])))
    # Save internal scores; the public payload intentionally exposes factual explanations only.
    snapshot = {
        'policy_version': h.matching_policy_version,
        'versions': {pid: record.version for pid, record in records.items()},
        'target': target,
        'profiles': {pid: profile_snapshot(profile) for pid, profile in profiles.items()},
    }
    run = RecommendationRun.objects.create(
        hackathon=h,
        viewer=viewer,
        algorithm_version=ALGORITHM_VERSION,
        snapshot=snapshot,
        snapshot_hash=digest(snapshot),
        results=chosen,
    )
    public = [{k: v for k, v in row.items() if k != 'score'} for row in chosen]
    return {'results': public, 'run_id': str(run.pk), 'algorithm_version': ALGORITHM_VERSION, 'unknown_fields': []}


def batch_suggestions(h, actor, top_k=8):
    if not is_organizer(actor, h):
        raise PermissionDenied
    require_enabled(h)
    if not h.teams_open:
        raise MatchingError('ROSTER_FROZEN', '名单已冻结。')
    profiles, records = core_profiles(h)
    cutoff = timezone.now() - timedelta(days=7)
    eligible = {
        pid: p
        for pid, p in profiles.items()
        if p.eligible and p.opt_in and records[pid].batch_opt_in and records[pid].confirmed_at >= cutoff
    }
    blocks = []
    for team in Team.objects.filter(hackathon=h).prefetch_related('members'):
        ids = frozenset(str(p.pk) for p in team.members.all())
        if not team.recruiting or team.state != 'recruiting' or len(ids) >= h.team_size or not ids <= eligible.keys():
            for pid in ids:
                eligible.pop(pid, None)
        elif ids:
            blocks.append(ids)
    pair_scores = {
        frozenset((a.person_id, b.person_id)): core.reciprocal_score(a, b)
        for a, b in combinations(eligible.values(), 2)
    }
    implemented = set(IdeaImplementation.objects.filter(hackathon=h).values_list('idea_id', flat=True))
    candidates = {}
    for idea in Idea.objects.filter(hackathon=h, state='open').select_related('hackathon').order_by('pk'):
        if idea.mode == 'recruit' and idea.pk in implemented:
            # A recruiting implementation may only be extended as its complete block.
            owner = eligible.get(str(idea.author_id))
            if owner is None or owner.team_id is None:
                continue
        if h.team_size < 2:
            continue
        concept = core_idea(idea)
        interested = [p for p in eligible.values() if p.interests and p.interests & concept.interests]
        for anchor in sorted(interested, key=lambda p: p.person_id):
            ranked = sorted(
                (p for p in interested if p.person_id != anchor.person_id and not core.pair_reasons(anchor, p)),
                key=lambda p: (-(pair_scores[frozenset((anchor.person_id, p.person_id))] or 0), p.person_id),
            )
            peers = ranked[:top_k]
            # Deterministic exploration beyond the most popular candidates.
            peers += sorted(ranked[top_k:], key=lambda p: (records[p.person_id].confirmed_at, p.person_id))[:2]
            groups = [(anchor, p) for p in peers]
            if h.team_size >= 3:
                groups.extend((anchor, a, b) for a, b in combinations(peers, 2))
            for seed_group in groups:
                ids = {p.person_id for p in seed_group}
                for block in blocks:
                    if ids & block:
                        ids.update(block)
                group = tuple(eligible[pid] for pid in sorted(ids))
                if core.group_reasons(
                    group,
                    concept,
                    max_size=h.team_size,
                    min_common_slots=h.matching_min_common_slots,
                    locked_blocks=blocks,
                ):
                    continue
                new_ids = frozenset(p.person_id for p in group if p.team_id is None)
                if not new_ids:
                    continue
                scores = [pair_scores[frozenset((a.person_id, b.person_id))] for a, b in combinations(group, 2)]
                if any(s is None or s < h.matching_min_pair_score for s in scores):
                    continue
                features = {'reciprocal': sum(scores) / len(scores), 'coverage': core.team_coverage(group, concept)}
                score = core.weighted_score(features, {'reciprocal': 0.65, 'coverage': 0.35}).value
                ids = frozenset(p.person_id for p in group)
                candidates[(tuple(sorted(ids)), str(idea.pk))] = core.Candidate(
                    ids, new_ids, score, str(idea.pk), features
                )
    ordered = [candidates[key] for key in sorted(candidates)]
    selected = core.greedy_pack(ordered)
    snapshot = {
        'policy_version': h.matching_policy_version,
        'versions': {pid: records[pid].version for pid in eligible},
        'candidate_count': len(ordered),
        'top_k': top_k,
        'roster_versions': {str(t.pk): t.roster_version for t in Team.objects.filter(hackathon=h)},
        'profiles': {pid: profile_snapshot(eligible[pid]) for pid in eligible},
        'seed': 20261017,
        'ideas': {str(i.pk): i.version for i in Idea.objects.filter(hackathon=h, state='open')},
    }
    # Persist the complete generated set so the selection can be replayed.
    snapshot['candidates'] = [
        {'members': sorted(c.members), 'newcomers': sorted(c.newcomer_ids), 'idea_id': c.idea_id, 'score': c.score}
        for c in ordered
    ]
    results = [
        {'member_ids': sorted(c.members), 'idea_id': c.idea_id, 'score': c.score, 'evidence': dict(c.evidence)}
        for c in selected
    ]
    run = RecommendationRun.objects.create(
        hackathon=h,
        viewer=None,
        algorithm_version=ALGORITHM_VERSION,
        snapshot_hash=digest(snapshot),
        snapshot=snapshot,
        results=results,
        state='feasible',
        diagnostics={
            'solver': 'deterministic_greedy',
            'optimality_proven': False,
            'scope': 'generated_candidates_only',
            'candidate_count': len(ordered),
            'newcomers_covered': sum(len(c.newcomer_ids) for c in selected),
        },
    )
    audit(h, actor, '生成待确认撮合建议', run.pk)
    return {'run_id': str(run.pk), 'state': run.state, 'results': results, 'diagnostics': run.diagnostics}


def delete_profile(h, actor):
    p = own_person(h, actor)
    MatchingContact.objects.filter(hackathon=h, participant=p).delete()
    MatchingProfile.objects.filter(hackathon=h, participant=p).delete()
    MatchingFeedback.objects.filter(hackathon=h, participant=p).delete()
    RecommendationRun.objects.filter(hackathon=h).delete()
    MatchIntent.objects.filter(hackathon=h, state__in=['pending', 'accepted']).filter(
        Q(sender=p) | Q(recipient=p)
    ).update(state='cancelled')
    TeamProposal.objects.filter(hackathon=h, state='awaiting_consent', snapshot__members__has_key=str(p.pk)).update(
        state='stale'
    )
    p.looking_for_team = False
    p.save(update_fields=['looking_for_team', 'updated'])
    emit(h, 'MatchingProfileDeleted', {'participant_id': str(p.pk)})
    return {'state': 'deleted'}


def configure(h, actor, enabled, roster, single, minimum, maximum, capacity=150):
    if not is_organizer(actor, h):
        raise PermissionDenied
    if h.matching_frozen_at:
        raise MatchingError('ROSTER_FROZEN', '名单已冻结，不能修改本轮政策。')
    if Team.objects.filter(hackathon=h).annotate(n=Count('members')).filter(n__gt=maximum).exists():
        raise MatchingError('TEAM_FULL', '现有队伍人数超过新上限，请先处理名单。')
    if Participant.objects.filter(hackathon=h, admission_state='admitted').count() > capacity:
        raise MatchingError('EVENT_FULL', '已录取人数超过新容量，请先处理资格。')
    h.matching_capacity = capacity
    h.matching_enabled, h.matching_roster_enabled, h.matching_single_allowed = enabled, roster, single
    h.matching_idea_min_chars, h.team_size = minimum, maximum
    h.matching_policy_version += 1
    h.save(
        update_fields=[
            'matching_capacity',
            'matching_enabled',
            'matching_roster_enabled',
            'matching_single_allowed',
            'matching_idea_min_chars',
            'team_size',
            'matching_policy_version',
            'updated',
        ]
    )
    audit(h, actor, '批准 / 修改匹配政策', h.slug, f'政策版本 {h.matching_policy_version}')
    return {'state': 'configured', 'policy_version': h.matching_policy_version}


def set_admission(h, actor, participant_id, state):
    if not is_organizer(actor, h):
        raise PermissionDenied
    p = person(h, participant_id)
    if (p.team_id and state != 'admitted') or h.matching_frozen_at or db_now() >= h.team_freeze_at:
        raise MatchingError('ROSTER_NOT_AVAILABLE', '请先按名单政策处理成员关系；冻结后不允许变更资格。')
    if (
        state == 'admitted'
        and p.admission_state != 'admitted'
        and Participant.objects.filter(hackathon=h, admission_state='admitted').count() >= h.matching_capacity
    ):
        raise MatchingError('EVENT_FULL', '正式录取名额已满。')
    p.admission_state = state
    p.save(update_fields=['admission_state', 'updated'])
    audit(h, actor, '更新录取资格', p.code, state)
    return {'state': state}


def excluded_ids(h, viewer):
    edges = (
        MatchingBlock.objects.filter(hackathon=h)
        .filter(Q(source=viewer) | Q(target=viewer))
        .values_list('source_id', 'target_id')
    )
    return {b if a == viewer.pk else a for a, b in edges}


def profile_snapshot(profile):
    return {
        'person_id': profile.person_id,
        'event_id': profile.event_id,
        'skills': profile.skills,
        'needs': profile.needs,
        'interests': sorted(profile.interests) if profile.interests is not None else None,
        'slots': sorted(profile.slots) if profile.slots is not None else None,
        'goal': profile.goal,
        'eligible': profile.eligible,
        'opt_in': profile.opt_in,
        'blocked_ids': sorted(profile.blocked_ids),
        'team_id': profile.team_id,
        'async_ok': profile.async_ok,
    }


def contacts_for_intents(h, viewer, intents):
    excluded = excluded_ids(h, viewer)
    accepted = [
        i
        for i in intents
        if i.state == 'accepted'
        and viewer.pk in {i.sender_id, i.recipient_id}
        and not ({i.sender_id, i.recipient_id} & excluded)
    ]
    ids = {pid for item in accepted for pid in (item.sender_id, item.recipient_id)}
    contacts = {
        c.participant_id: c.text
        for c in MatchingContact.objects.filter(hackathon=h, participant_id__in=ids, share_on_accept=True)
    }
    return {
        i.pk: {str(pid): contacts[pid] for pid in (i.sender_id, i.recipient_id) if pid in contacts} for i in accepted
    }


def transfer_captain(h, actor, participant_id):
    require_open(h)
    leader = own_person(h, actor)
    members = (
        list(Participant.objects.select_for_update().filter(hackathon=h, team_id=leader.team_id).order_by('pk'))
        if leader.team_id
        else []
    )
    team = Team.objects.select_for_update().filter(hackathon=h, pk=leader.team_id, captain=leader).first()
    if not team:
        raise PermissionDenied
    successor = next((p for p in members if p.pk == participant_id and p.pk != leader.pk), None)
    if successor is None:
        raise MatchingError('NOT_AVAILABLE', '请选择当前队伍中的另一位成员。', 404)
    team.captain = successor
    team.roster_version += 1
    team.save(update_fields=['captain', 'roster_version', 'updated'])
    emit(h, 'TeamCaptainTransferred', {'team_id': str(team.pk), 'captain_id': str(successor.pk)})
    audit(h, actor, '转交队长', team.code, successor.code)
    return {'state': 'transferred', 'captain_id': str(successor.pk)}
