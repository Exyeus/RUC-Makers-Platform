"""Competition day runs on its own once the plan is set."""

import random
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from eventyay.hackathon import competition_day
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import Project, Track
from eventyay.hackathon.workflow_models import CompetitionPlan, JudgingRound
from tests.hackathon.test_judging_workflow import fill, make_judges, make_projects, shortlist
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()


def plan_with(hackathon, organizer_user, judges_per_track=2):
    projects = make_projects(hackathon, count=4, tracks=2)
    Project.objects.filter(hackathon=hackathon).update(state=Project.State.SUBMITTED)
    tracks = list(Track.objects.filter(hackathon=hackathon).order_by('pk'))
    judges = make_judges(hackathon, judges_per_track * len(tracks))
    panel = {
        str(track.pk): [str(j.pk) for j in judges[i * judges_per_track : (i + 1) * judges_per_track]]
        for i, track in enumerate(tracks)
    }
    plan = CompetitionPlan.objects.create(
        hackathon=hackathon,
        preliminary_panel=panel,
        final_panel=[str(j.pk) for j in judges],
        signer=organizer_user,
        seats=2,
        alternates=1,
        roadshow_start=timezone.now() + timedelta(hours=2),
        slot_minutes=5,
        updated_by=organizer_user,
    )
    return plan, projects, judges


def test_nothing_happens_before_the_deadline(hackathon, organizer_user):
    plan_with(hackathon, organizer_user)
    hackathon.submission_deadline = timezone.now() + timedelta(hours=1)
    hackathon.save()
    assert competition_day.lock_and_start(hackathon) == 'waiting'
    assert not JudgingRound.objects.exists()


def test_deadline_locks_accepts_ready_work_and_opens_the_preliminary_round_once(hackathon, organizer_user):
    plan, projects, judges = plan_with(hackathon, organizer_user)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    assert competition_day.lock_and_start(hackathon) == 'done'
    item = JudgingRound.objects.get(hackathon=hackathon, kind='preliminary')
    assert item.is_open and len(item.tasks) == len(projects)
    assert all(len(task['judges']) == 2 for task in item.tasks)
    assert set(Project.objects.values_list('state', flat=True)) == {'approved'}
    plan.refresh_from_db()
    assert plan.locked_at and plan.last_error == ''
    assert competition_day.lock_and_start(hackathon) == 'done'
    assert JudgingRound.objects.filter(hackathon=hackathon).count() == 1


def test_incomplete_plan_records_why_and_retries_later(hackathon, organizer_user):
    plan, _projects, _judges = plan_with(hackathon, organizer_user)
    first_track = next(iter(plan.preliminary_panel))
    plan.preliminary_panel[first_track] = plan.preliminary_panel[first_track][:1]
    plan.save()
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    assert competition_day.lock_and_start(hackathon) == 'failed'
    plan.refresh_from_db()
    assert '少于 2 位' in plan.last_error and plan.locked_at is None
    assert not JudgingRound.objects.exists()


def test_first_page_view_after_the_deadline_runs_the_step(client, hackathon, organizer_user):
    plan_with(hackathon, organizer_user)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    client.get(endpoint(hackathon, 'schedule'))
    assert JudgingRound.objects.filter(hackathon=hackathon, is_open=True).exists()


def test_roadshow_order_is_random_once_and_covers_every_finalist(hackathon, organizer_user):
    plan, projects, judges = plan_with(hackathon, organizer_user)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    competition_day.lock_and_start(hackathon)
    item = JudgingRound.objects.get(hackathon=hackathon, kind='preliminary')
    fill(hackathon, item, judges, projects, value=lambda project: projects.index(project) % 10)
    shortlist(hackathon, organizer_user, item, seats=2)
    order = competition_day.shuffle_order(hackathon, organizer_user, rng=random.Random(7))
    finalists = {row['project_id'] for row in workflow.signed_entries(workflow.current_signed_snapshot(hackathon))}
    assert {row['project_id'] for row in order.entries} == finalists
    assert [row['position'] for row in order.entries] == list(range(1, len(finalists) + 1))
    again = competition_day.shuffle_order(hackathon, organizer_user, rng=random.Random(99))
    assert again.pk == order.pk and again.entries == order.entries
    start = competition_day.expected_start(plan, 3)
    assert start == plan.roadshow_start + timedelta(minutes=10)
