"""Competition day on its own: what happens at the submission deadline and after.

The organizer sets a CompetitionPlan in advance. At the deadline the platform
freezes a material snapshot, accepts every submission whose materials passed
the automatic checks, creates the preliminary round from the plan and opens it.
Every step is idempotent; a failed step records why and is retried on the next
tick (management command or the next page view after the deadline).
"""

import logging
import random
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from eventyay.hackathon import services
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import DemoSlot, Project, StaffAssignment, Track
from eventyay.hackathon.submission_materials import capture_snapshot
from eventyay.hackathon.workflow_forms import ROUND_CRITERIA
from eventyay.hackathon.workflow_models import CompetitionPlan, FinalistContact, JudgingRound, RoadshowOrder


logger = logging.getLogger(__name__)
DONE_KEY = 'hackathon-competition-locked-{pk}'


def plan_for(hackathon):
    return CompetitionPlan.objects.filter(hackathon=hackathon).select_related('signer', 'updated_by').first()


def round_rules(kind, plan):
    return {
        'criteria': [{'key': key, 'label': label, 'weight': weight} for key, label, weight in ROUND_CRITERIA[kind]],
        'aggregation': 'mean',
        'trim': 0,
        'minimum_judges': 2 if kind == 'preliminary' else 1,
        'minimum_nonstudent': 0,
        'reviewers': [
            {'user_id': user_id, 'tracks': [track_id], 'nonstudent': True}
            for track_id, user_id in (plan.reviewers or {}).items()
            if user_id
        ],
        'source': 'plan',
    }


def preliminary_panel(plan):
    tracks_by_judge = {}
    for track_id, judges in (plan.preliminary_panel or {}).items():
        for user_id in judges:
            tracks_by_judge.setdefault(str(user_id), []).append(str(track_id))
    return [{'user_id': user_id, 'tracks': tracks, 'nonstudent': True} for user_id, tracks in tracks_by_judge.items()]


def plan_problems(hackathon, plan):
    """Why the plan cannot run yet, in organizer words. Empty when ready."""
    if plan is None:
        return ['还没有比赛日配置。']
    problems = []
    judges = set(
        str(pk)
        for pk in StaffAssignment.objects.filter(
            hackathon=hackathon, role='judge', user__is_active=True, user__is_spam=False
        ).values_list('user_id', flat=True)
    )
    for track in Track.objects.filter(hackathon=hackathon, state='published'):
        chosen = [str(pk) for pk in (plan.preliminary_panel or {}).get(str(track.pk), [])]
        if len(chosen) < 2:
            problems.append(f'{track.name}：初评评委少于 2 位。')
        if set(chosen) - judges:
            problems.append(f'{track.name}：有初评评委已不是本届评委。')
    if not plan.signer_id:
        problems.append('未指定签认名单的评审总负责人。')
    return problems


def accept_ready_submissions(hackathon, actor, snapshot):
    """Projects whose frozen materials passed every automatic check enter judging without a manual review."""
    ready = [row['project_id'] for row in snapshot.manifest['entries'] if row['eligibility'] == 'ready']
    changed = Project.objects.filter(hackathon=hackathon, pk__in=ready, state=Project.State.SUBMITTED).update(
        state=Project.State.APPROVED
    )
    services.audit(hackathon, actor, '截止后自动通过材料检查', snapshot.pk, f'{changed} 件')
    return changed


def lock_and_start(hackathon, now=None):
    """Run the deadline step once. Returns 'done', 'waiting' or 'failed'."""
    now = now or timezone.now()
    if now < hackathon.submission_deadline:
        return 'waiting'
    plan = plan_for(hackathon)
    if plan is None or not plan.auto_lock:
        return 'waiting'
    if plan.locked_at:
        return 'done'
    try:
        with transaction.atomic():
            plan = CompetitionPlan.objects.select_for_update().get(pk=plan.pk)
            if plan.locked_at:
                return 'done'
            problems = plan_problems(hackathon, plan)
            if problems:
                raise ValidationError(problems)
            actor = plan.updated_by
            existing = JudgingRound.objects.filter(hackathon=hackathon, kind='preliminary').first()
            if existing is None:
                snapshot = capture_snapshot(hackathon, actor, '提交截止自动锁定')
                accept_ready_submissions(hackathon, actor, snapshot)
                item = workflow.create_round(
                    hackathon,
                    actor,
                    kind='preliminary',
                    name='初评',
                    rules=round_rules('preliminary', plan),
                    panel=preliminary_panel(plan),
                    signer=plan.signer,
                    signer_nonstudent=True,
                )
                workflow.set_round_open(hackathon, actor, item, True)
            plan.locked_at = timezone.now()
            plan.last_error = ''
            plan.last_attempt_at = plan.locked_at
            plan.save(update_fields=['locked_at', 'last_error', 'last_attempt_at', 'updated'])
            services.audit(hackathon, actor, '比赛日：自动锁定并开放初评', plan.pk)
        cache.set(DONE_KEY.format(pk=hackathon.pk), True, 3600)
        return 'done'
    except ValidationError as error:
        message = '；'.join(error.messages)[:500]
        CompetitionPlan.objects.filter(pk=plan.pk).update(last_error=message, last_attempt_at=timezone.now())
        logger.warning('Competition day lock failed for %s: %s', hackathon.slug, message)
        return 'failed'


def tick(hackathon):
    """Cheap enough for page views: no query before the deadline or after the step ran."""
    if timezone.now() < hackathon.submission_deadline or cache.get(DONE_KEY.format(pk=hackathon.pk)):
        return None
    return lock_and_start(hackathon)


# ---------- Roadshow order ----------


def expected_start(plan, position):
    if not plan or not plan.roadshow_start:
        return None
    return plan.roadshow_start + timedelta(minutes=plan.slot_minutes * (position - 1))


def current_order(hackathon):
    snapshot = workflow.current_signed_snapshot(hackathon)
    if not snapshot:
        return None
    return RoadshowOrder.objects.filter(hackathon=hackathon, snapshot=snapshot).first()


@transaction.atomic
def shuffle_order(hackathon, actor, rng=None):
    """Shuffle all signed finalists once into one queue. A second call returns the same order."""
    workflow.require_organizer(hackathon, actor)
    h = services.lock_event(hackathon)
    snapshot = workflow.current_signed_snapshot(h)
    if not snapshot:
        raise ValidationError('请先确认并签认入围名单。')
    existing = RoadshowOrder.objects.filter(hackathon=h, snapshot=snapshot).first()
    if existing:
        return existing
    finalists = [row for row in workflow.signed_entries(snapshot)]
    if not finalists:
        raise ValidationError('入围名单为空。')
    ids = [row['project_id'] for row in finalists]
    (rng or random.SystemRandom()).shuffle(ids)
    rows = {row['project_id']: row for row in finalists}
    entries = [
        {
            'position': index,
            'project_id': project_id,
            'team_id': rows[project_id]['team_id'],
            'team_code': rows[project_id].get('team_code', ''),
            'team': rows[project_id].get('team', ''),
            'captain': rows[project_id].get('captain', ''),
            'title': rows[project_id].get('title', ''),
            'track_id': rows[project_id]['track_id'],
            'final_material_kind': rows[project_id].get('final_material_kind', ''),
        }
        for index, project_id in enumerate(ids, 1)
    ]
    previous = RoadshowOrder.objects.filter(hackathon=h).first()
    order = RoadshowOrder.objects.create(
        hackathon=h,
        snapshot=snapshot,
        version=(previous.version + 1) if previous else 1,
        entries=entries,
        reason='随机生成',
        created_by=actor,
        supersedes=previous,
    )
    services.audit(h, actor, '生成随机路演顺序', order.pk, f'v{order.version} · {len(entries)} 队')
    return order


@transaction.atomic
def publish_order(hackathon, actor, order):
    """Publish the signed list and its order together: queue, in-site notices and confirmation windows."""
    workflow.require_organizer(hackathon, actor)
    h = services.lock_event(hackathon)
    order = RoadshowOrder.objects.select_for_update().get(hackathon=h, pk=order.pk)
    if order.snapshot_id != getattr(workflow.current_signed_snapshot(h), 'pk', None):
        raise ValidationError('入围名单已更新，请为新名单生成路演顺序。')
    if order.published_at:
        return order
    if h.results_published:
        raise ValidationError('成绩已公布，路演队列已锁定。')
    slots = list(DemoSlot.objects.filter(hackathon=h))
    if any(slot.state in {'live', 'done'} for slot in slots):
        raise ValidationError('路演已经开始，不能整体替换队列。')
    wanted = [row['project_id'] for row in order.entries]
    DemoSlot.objects.filter(hackathon=h).exclude(project_id__in=wanted).delete()
    for row in order.entries:
        DemoSlot.objects.update_or_create(
            hackathon=h, project_id=row['project_id'], defaults={'position': row['position']}
        )
    plan = plan_for(h)
    now = timezone.now()
    window = timedelta(minutes=plan.confirm_minutes if plan else 15)
    FinalistContact.objects.filter(hackathon=h, snapshot=order.snapshot, state='pending').update(
        state='sent', channel='站内通知', sent_at=now, deadline=now + window
    )
    order.published_at = now
    order.save(update_fields=['published_at', 'updated'])
    services.audit(h, actor, '发布入围名单与路演顺序', order.pk, f'v{order.version}')
    return order


def team_result(hackathon, team):
    """What a team sees once the list is published: finalist with slot, alternate, or not selected."""
    if not team:
        return None
    order = current_order(hackathon)
    if not order or not order.published_at:
        return None
    snapshot = order.snapshot
    row = next((entry for entry in snapshot.manifest['entries'] if entry['team_id'] == str(team.pk)), None)
    if row is None:
        return None
    result = {'status': row['status'], 'snapshot': snapshot, 'order': order}
    if row['status'] == 'finalist':
        slot = next((entry for entry in order.entries if entry['team_id'] == str(team.pk)), None)
        plan = plan_for(hackathon)
        result['position'] = slot['position'] if slot else None
        result['total'] = len(order.entries)
        result['expected'] = expected_start(plan, slot['position']) if slot else None
        result['contact'] = FinalistContact.objects.filter(hackathon=hackathon, snapshot=snapshot, team=team).first()
    return result


# ---------- Final round ----------


@transaction.atomic
def start_final(hackathon, actor):
    """Create the final round from the plan's panel over the signed finalists and open it."""
    workflow.require_organizer(hackathon, actor)
    plan = plan_for(hackathon)
    if not plan or not plan.final_panel:
        raise ValidationError('请先在比赛日配置中选择决赛评委。')
    if not plan.signer_id:
        raise ValidationError('请先在比赛日配置中指定评审总负责人。')
    existing = JudgingRound.objects.filter(hackathon=hackathon, kind='final').first()
    if existing and existing.source_snapshot_id == getattr(workflow.current_signed_snapshot(hackathon), 'pk', None):
        if not existing.is_open and not existing.snapshots.exists():
            workflow.set_round_open(hackathon, actor, existing, True)
        return existing
    item = workflow.create_round(
        hackathon,
        actor,
        kind='final',
        name='决赛',
        rules=round_rules('final', plan),
        panel=[{'user_id': str(pk), 'tracks': [], 'nonstudent': True} for pk in plan.final_panel],
        signer=plan.signer,
        signer_nonstudent=True,
    )
    workflow.set_round_open(hackathon, actor, item, True)
    return item


# ---------- Command center ----------


def round_progress(item):
    if not item:
        return None
    rows = workflow.round_results(item)
    expected = sum(row['expected'] for row in rows)
    confirmed = sum(row['confirmed'] for row in rows)
    remaining = {}
    for task in item.tasks:
        for judge_id in task['judges']:
            remaining.setdefault(judge_id, 0)
    for row in rows:
        for judge_id in row['missing']:
            remaining[judge_id] = remaining.get(judge_id, 0) + 1
    names = {row['user_id']: row.get('name', '') for row in item.panel}
    return {
        'round': item,
        'rows': rows,
        'expected': expected,
        'confirmed': confirmed,
        'percent': round(confirmed * 100 / expected) if expected else 0,
        'left': expected - confirmed,
        'incomplete': sum(not row['complete'] for row in rows),
        'judges': sorted(
            ({'name': names.get(key, key), 'left': value} for key, value in remaining.items()),
            key=lambda judge: -judge['left'],
        ),
    }


def status(hackathon):
    """Where competition day stands and the single next action."""
    now = timezone.now()
    plan = plan_for(hackathon)
    preliminary = JudgingRound.objects.filter(hackathon=hackathon, kind='preliminary').first()
    final = JudgingRound.objects.filter(hackathon=hackathon, kind='final').first()
    finalists = workflow.current_signed_snapshot(hackathon)
    awards = workflow.current_signed_snapshot(hackathon, 'awards')
    order = current_order(hackathon)
    if awards:
        stage = 'done'
    elif now < hackathon.submission_deadline or not preliminary:
        stage = 'deadline'
    elif preliminary.is_open:
        stage = 'preliminary'
    elif not finalists:
        stage = 'review'
    elif not order or not order.published_at:
        stage = 'publish'
    elif not final:
        stage = 'roadshow'
    elif final.is_open:
        stage = 'final'
    else:
        stage = 'awards'
    contacts = (
        list(FinalistContact.objects.filter(hackathon=hackathon, snapshot=finalists).select_related('team'))
        if finalists
        else []
    )
    return {
        'now': now,
        'stage': stage,
        'plan': plan,
        'problems': plan_problems(hackathon, plan),
        'preliminary': round_progress(preliminary),
        'final': round_progress(final),
        'finalists': finalists,
        'order': order,
        'awards': awards,
        'contacts': contacts,
        'accepted': sum(contact.state == 'accepted' for contact in contacts),
    }
