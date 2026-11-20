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


def ready_for_publish(hackathon, organizer_user):
    plan, projects, judges = plan_with(hackathon, organizer_user)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    competition_day.lock_and_start(hackathon)
    item = JudgingRound.objects.get(hackathon=hackathon, kind='preliminary')
    fill(hackathon, item, judges, projects, value=lambda project: projects.index(project) % 10)
    shortlist(hackathon, organizer_user, item, seats=2)
    return plan, projects, judges


def test_publish_queues_the_order_notifies_finalists_and_captain_confirms_from_overview(
    client, hackathon, organizer_user
):
    from eventyay.hackathon.models import DemoSlot, Team
    from eventyay.hackathon.workflow_models import FinalistContact

    plan, projects, _judges = ready_for_publish(hackathon, organizer_user)
    order = competition_day.shuffle_order(hackathon, organizer_user)
    competition_day.publish_order(hackathon, organizer_user, order)
    slots = {str(slot.project_id): slot.position for slot in DemoSlot.objects.filter(hackathon=hackathon)}
    assert slots == {row['project_id']: row['position'] for row in order.entries}
    contacts = FinalistContact.objects.filter(hackathon=hackathon)
    assert contacts.count() == len(order.entries)
    assert all(contact.state == 'sent' and contact.deadline for contact in contacts)
    first = order.entries[0]
    captain = Team.objects.get(pk=first['team_id']).captain.user
    client.force_login(captain)
    page = client.get(endpoint(hackathon, 'home')).content.decode()
    assert '已入围决赛' in page and '确认参加' in page
    response = client.post(endpoint(hackathon, 'workflow-confirm', snapshot_id=order.snapshot_id), {'return': 'home'})
    assert response.status_code == 302 and response.url == endpoint(hackathon, 'home')
    assert FinalistContact.objects.get(hackathon=hackathon, team_id=first['team_id']).state == 'accepted'


def test_command_center_walks_the_day_with_one_action_per_stage(client, hackathon, organizer_user):
    plan, projects, judges = ready_for_publish(hackathon, organizer_user)
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'competition-day')
    assert '生成随机路演顺序' in client.get(location).content.decode()
    client.post(location, {'action': 'shuffle'})
    assert '发布名单与顺序' in client.get(location).content.decode()
    client.post(location, {'action': 'publish'})
    assert '开始决赛评分' in client.get(location).content.decode()
    client.post(location, {'action': 'start-final'})
    final = JudgingRound.objects.get(hackathon=hackathon, kind='final')
    assert final.is_open and len(final.tasks) == len(competition_day.current_order(hackathon).entries)
    assert all(len(task['judges']) == len(plan.final_panel) for task in final.tasks)
    assert '关闭决赛评分' in client.get(location).content.decode()


def test_command_center_is_organizer_only(client, hackathon, user):
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'competition-day')).status_code in {302, 403}
    assert client.post(endpoint(hackathon, 'competition-day'), {'action': 'shuffle'}).status_code in {302, 403}


def test_an_approved_work_with_failed_materials_does_not_block_the_round(hackathon, organizer_user):
    from eventyay.hackathon.models import MaterialPolicy

    plan, projects, _judges = plan_with(hackathon, organizer_user)
    MaterialPolicy.objects.create(hackathon=hackathon, enabled=True)
    Project.objects.filter(pk=projects[0].pk).update(state=Project.State.APPROVED)
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    # Every synthetic work lacks materials under the policy: nothing is ready, so the plan reports it.
    assert competition_day.lock_and_start(hackathon) == 'failed'
    plan.refresh_from_db()
    assert '没有可供本轮评审' in plan.last_error


def test_roadshow_package_names_files_in_order_and_lists_them(
    client, hackathon, organizer_user, settings, tmp_path, monkeypatch
):
    import csv
    import hashlib
    import io
    import zipfile

    from django.core.files.uploadedfile import SimpleUploadedFile

    from eventyay.hackathon.models import Asset
    from eventyay.hackathon.storage import PrivateStorage

    settings.DATA_DIR = str(tmp_path)
    monkeypatch.setattr(Asset._meta.get_field('file'), 'storage', PrivateStorage())
    plan, projects, judges = plan_with(hackathon, organizer_user)
    for project in projects:
        data = f'%PDF-{project.pk}'.encode()
        item = Asset(
            hackathon=hackathon,
            project=project,
            kind='slides',
            filename='deck.pdf',
            original_filename='路演.pdf',
            checksum=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
        item.file.save('deck.pdf', SimpleUploadedFile('deck.pdf', data))
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    competition_day.lock_and_start(hackathon)
    item = JudgingRound.objects.get(hackathon=hackathon, kind='preliminary')
    fill(hackathon, item, judges, projects, value=lambda project: projects.index(project) % 10)
    shortlist(hackathon, organizer_user, item, seats=2)
    order = competition_day.shuffle_order(hackathon, organizer_user)
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'roadshow-package'))
    assert response.status_code == 200
    assert '%E8%B7%AF%E6%BC%94%E6%9D%90%E6%96%99' in response['Content-Disposition']
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    names = sorted(name for name in archive.namelist() if name.endswith('.pdf'))
    assert [name[:3] for name in names] == [f'{row["position"]:03d}' for row in order.entries]
    first = order.entries[0]
    assert names[0] == f'001_{first["team_code"]}_{first["captain"]}_{first["title"]}.pdf'
    rows = list(csv.reader(io.StringIO(archive.read('路演材料清单.csv').decode('utf-8-sig'))))
    assert rows[0][0] == '路演序号' and len(rows) == len(order.entries) + 1


def test_roadshow_package_refuses_when_a_finalist_has_no_material(client, hackathon, organizer_user):
    plan, projects, judges = ready_for_publish(hackathon, organizer_user)
    competition_day.shuffle_order(hackathon, organizer_user)
    client.force_login(organizer_user)
    response = client.get(endpoint(hackathon, 'roadshow-package'))
    assert response.status_code == 302
    follow = client.get(response.url).content.decode()
    assert '路演材料包未生成' in follow and '没有路演材料' in follow
