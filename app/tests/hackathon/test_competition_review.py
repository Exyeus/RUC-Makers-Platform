"""Finalist review and award confirmation from the command center."""

import csv
import io
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from eventyay.hackathon import competition_day
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.workflow_models import JudgingRound
from tests.hackathon.test_competition_day import plan_with
from tests.hackathon.test_judging_workflow import fill
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()


def closed_preliminary(hackathon, organizer_user, value=8, seats=1, alternates=0, signer=None):
    plan, projects, judges = plan_with(hackathon, organizer_user)
    plan.seats, plan.alternates = seats, alternates
    if signer is not None:
        plan.signer = judges[signer]
    plan.save()
    hackathon.submission_deadline = timezone.now() - timedelta(minutes=1)
    hackathon.save()
    competition_day.lock_and_start(hackathon)
    item = JudgingRound.objects.get(hackathon=hackathon, kind='preliminary')
    fill(hackathon, item, judges, projects, value=lambda p: value(projects.index(p)) if callable(value) else value)
    workflow.set_round_open(hackathon, organizer_user, item, False)
    return plan, projects, judges, item


def test_tie_on_the_cut_line_asks_for_an_order_and_a_basis(client, hackathon, organizer_user):
    _plan, projects, _judges, item = closed_preliminary(hackathon, organizer_user)
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'competition-review')
    page = client.get(location).content.decode()
    assert 'name="tie_' in page and '同分裁定依据' in page
    page = client.post(location, {'action': 'generate', 'reason': ''}).content.decode()
    assert '请为同分作品填写复核顺序' in page
    rows = workflow.round_results(item)
    track = rows[0]['track_id']
    tied = [row['project_id'] for row in rows if row['track_id'] == track]
    data = {'action': 'generate', 'reason': '', 'tie_note': '现场复核：演示完成度更高。'}
    for key in {row['track_id'] for row in rows}:
        ids = [row['project_id'] for row in rows if row['track_id'] == key]
        data.update({f'tie_{pid}': str(len(ids) - index) for index, pid in enumerate(ids)})
    response = client.post(location, data)
    assert response.status_code == 302, response.content.decode().split('form-errors')[-1][:400]
    page = client.get(location).content.decode()
    assert '待签认' in page and '签认入围名单' in page
    waiting = workflow.ResultSnapshot.objects.get(hackathon=hackathon, kind='finalists')
    entries = sorted(
        (row for row in waiting.manifest['entries'] if row['track_id'] == track), key=lambda row: row['position']
    )
    assert [row['project_id'] for row in entries] == tied[::-1]
    assert entries[0]['status'] == 'finalist' and '同分裁定' in waiting.reason
    response = client.post(location, {'action': 'sign', 'sha256': waiting.sha256})
    assert response.url == endpoint(hackathon, 'competition-day')
    assert competition_day.status(hackathon)['stage'] == 'publish'


def test_clear_ranking_generates_without_tie_inputs(client, hackathon, organizer_user):
    _plan, projects, _judges, item = closed_preliminary(hackathon, organizer_user, value=lambda index: 3 + index)
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'competition-review')
    assert 'name="tie_' not in client.get(location).content.decode()
    assert client.post(location, {'action': 'generate', 'reason': ''}).status_code == 302
    waiting = workflow.ResultSnapshot.objects.get(hackathon=hackathon, kind='finalists')
    assert '前 1 名入围' in waiting.reason
    assert sum(row['status'] == 'finalist' for row in waiting.manifest['entries']) == 2


def test_signer_without_organizer_rights_signs_from_the_judging_desk(client, hackathon, organizer_user, user):
    plan, projects, judges, item = closed_preliminary(
        hackathon, organizer_user, value=lambda index: 3 + index, signer=0
    )
    client.force_login(organizer_user)
    client.post(endpoint(hackathon, 'competition-review'), {'action': 'generate', 'reason': ''})
    client.force_login(judges[0])
    desk = client.get(endpoint(hackathon, 'workflow-judging')).content.decode()
    assert '核对并签认' in desk
    page = client.get(endpoint(hackathon, 'competition-review')).content.decode()
    assert '签认入围名单' in page and 'name="reason"' not in page
    waiting = workflow.ResultSnapshot.objects.get(hackathon=hackathon, kind='finalists')
    client.post(endpoint(hackathon, 'competition-review'), {'action': 'sign', 'sha256': waiting.sha256})
    waiting.refresh_from_db()
    assert waiting.signed_by == judges[0]
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'competition-review')).status_code in {302, 403}


def test_awards_are_suggested_by_rank_signed_and_exported(client, hackathon, organizer_user):
    plan, projects, judges, item = closed_preliminary(hackathon, organizer_user, value=lambda index: 3 + index, seats=2)
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'competition-review')
    client.post(location, {'action': 'generate', 'reason': ''})
    waiting = workflow.ResultSnapshot.objects.get(hackathon=hackathon, kind='finalists')
    client.post(location, {'action': 'sign', 'sha256': waiting.sha256})
    order = competition_day.shuffle_order(hackathon, organizer_user)
    competition_day.publish_order(hackathon, organizer_user, order)
    competition_day.start_final(hackathon, organizer_user)
    final = JudgingRound.objects.get(hackathon=hackathon, kind='final')
    fill(hackathon, final, judges, projects, value=lambda project: 2 + projects.index(project))
    workflow.set_round_open(hackathon, organizer_user, final, False)
    assert competition_day.status(hackathon)['stage'] == 'awards'
    page = client.get(location).content.decode()
    assert 'value="一等奖"' in page and 'value="二等奖"' in page
    rows = workflow.round_results(final)
    data = {'action': 'generate', 'reason': ''}
    data.update({f'award_{row["project_id"]}': ['一等奖', '二等奖'][row['rank'] - 1] for row in rows})
    assert client.post(location, data).status_code == 302
    awards = workflow.ResultSnapshot.objects.get(hackathon=hackathon, kind='awards')
    client.post(location, {'action': 'sign', 'sha256': awards.sha256})
    assert competition_day.status(hackathon)['stage'] == 'done'
    response = client.get(endpoint(hackathon, 'awards-export'))
    table = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert table[0][:3] == ['赛道', '决赛名次', '奖项']
    assert sorted(row[2] for row in table[1:]) == ['一等奖', '一等奖', '二等奖', '二等奖']
