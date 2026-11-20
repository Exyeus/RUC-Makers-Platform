"""Competition day on its own: what happens at the submission deadline and after.

The organizer sets a CompetitionPlan in advance. At the deadline the platform
freezes a material snapshot, accepts every submission whose materials passed
the automatic checks, creates the preliminary round from the plan and opens it.
Every step is idempotent; a failed step records why and is retried on the next
tick (management command or the next page view after the deadline).
"""

import logging
import random

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from eventyay.hackathon import services
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import Project, StaffAssignment, Track
from eventyay.hackathon.submission_materials import capture_snapshot
from eventyay.hackathon.workflow_forms import ROUND_CRITERIA
from eventyay.hackathon.workflow_models import CompetitionPlan, JudgingRound, RoadshowOrder


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
    return plan.roadshow_start + timezone.timedelta(minutes=plan.slot_minutes * (position - 1))


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
