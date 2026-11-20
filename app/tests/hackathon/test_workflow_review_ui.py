"""Role-specific UI must reflect existing scoring duties and immutable read access."""

import hashlib
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import Asset, Participant, StaffAssignment, SubmissionSnapshot
from eventyay.hackathon.workflow_models import RoundScore
from tests.hackathon.test_judging_workflow import endpoint, make_judges, make_projects, rules


pytestmark = pytest.mark.django_db


@pytest.fixture
def review_ui(hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=2)
    members = make_judges(hackathon, 5)
    judges, reviewer = members[:4], members[4]
    signer = User.objects.create_user(email='ui-signatory@example.invalid', fullname='非学生签认负责人')
    assets = []
    for index, project in enumerate(projects):
        contents = b'synthetic-auth-only-clip'
        assets.append(
            Asset.objects.create(
                hackathon=hackathon,
                project=project,
                kind='preliminary',
                file=SimpleUploadedFile(f'clip{index}.mp4', contents),
                filename=f'clip{index}.mp4',
                checksum=hashlib.sha256(contents).hexdigest(),
                size=len(contents),
            )
        )
    # Synthetic snapshot eligibility isolates UI/auth behavior; these bytes are not a valid-video fixture.
    SubmissionSnapshot.objects.create(
        hackathon=hackathon,
        version=1,
        captured_at=timezone.now(),
        captured_by=organizer_user,
        reason='只读复核UI合成夹具',
        sha256='f' * 64,
        manifest={
            'entries': [
                {
                    'project_id': str(project.pk),
                    'team_id': str(project.team_id),
                    'eligibility': 'ready',
                    'final_material_kind': 'slides',
                    'assets': [
                        {
                            'id': str(asset.pk),
                            'kind': asset.kind,
                            'filename': asset.filename,
                            'storage_name': asset.file.name,
                            'size': asset.size,
                            'sha256': asset.checksum,
                        }
                    ],
                }
                for project, asset in zip(projects, assets)
            ]
        },
    )
    config = rules('preliminary', organizer_user)
    config['reviewers'] = [
        {'user_id': str(reviewer.pk), 'tracks': [str(projects[0].team.track_id)], 'nonstudent': True},
        {'user_id': str(organizer_user.pk), 'tracks': [str(projects[1].team.track_id)], 'nonstudent': True},
    ]
    round = workflow.create_round(
        hackathon,
        organizer_user,
        kind='preliminary',
        name='初评UI验收',
        rules=config,
        panel=[
            {
                'user_id': str(judge.pk),
                'tracks': [str(projects[index // 2].team.track_id)],
                'nonstudent': False,
            }
            for index, judge in enumerate(judges)
        ],
        signer=signer,
        signer_nonstudent=True,
    )
    workflow.set_round_open(hackathon, organizer_user, round, True)
    values = {criterion['key']: 8 for criterion in round.rules['criteria']}
    saved = workflow.save_round_score(
        hackathon,
        judges[0],
        round,
        projects[0],
        values=values,
        final=True,
        revision=0,
        feedback='已保存的团队建议',
    )
    return SimpleNamespace(
        h=hackathon,
        lead=organizer_user,
        round=round,
        projects=projects,
        judges=judges,
        reviewer=reviewer,
        signer=signer,
        saved=saved,
        values=values,
    )


def read(client, fixture, user, route, project=0):
    client.force_login(user)
    kwargs = {'round_id': fixture.round.pk} if route != 'judging' else {}
    if route == 'score':
        kwargs['project_id'] = fixture.projects[project].pk
    response = client.get(endpoint(fixture.h, route, **kwargs))
    assert response.status_code == 200
    return response, BeautifulSoup(response.content, 'html.parser')


def test_reviewer_tasks_are_read_only_materials_without_personal_scoring_debt(client, review_ui):
    _, soup = read(client, review_ui, review_ui.reviewer, 'tasks')
    text = soup.get_text(' ', strip=True)
    assert '本轮可复核1份材料' in text
    assert '应评' not in text
    assert '待评' not in text
    assert '并评分' not in text
    assert '复核已完成' not in text
    assert '本轮打分任务已全部确认' not in text
    assert '查看材料与原评委评分' in text
    assert '功能与完成度' in text
    assert '8 / 10' in text


def test_reviewer_score_page_shows_source_scores_without_blank_editable_form(client, review_ui):
    _, soup = read(client, review_ui, review_ui.reviewer, 'score')
    panel = soup.select_one('[data-score-panel]')
    assert panel is not None
    assert not panel.select('form,input,textarea,select')
    assert '原评委评分（复核只读）' in panel.get_text()
    assert '8 / 10' in panel.get_text()
    assert '未填写' not in panel.get_text()
    assert '保存本轮草稿' not in panel.get_text()
    assert '确认本轮评分' not in panel.get_text()
    assert '本轮评分窗口开放中' in soup.get_text()
    assert '本轮窗口已关闭' not in soup.get_text()


def test_reviewer_round_picker_labels_role_and_material_entry(client, review_ui):
    _, soup = read(client, review_ui, review_ui.reviewer, 'judging')
    text = soup.get_text(' ', strip=True)
    assert '独立复核 / 名单签认，只读' in text
    assert '无需提交个人评分' in text
    assert '进入本轮复核资料' in text
    assert '进入本轮评分任务' not in text


@pytest.mark.parametrize('closed_by', ['window', 'published'])
def test_closed_or_published_judge_reads_saved_values_without_editable_controls(client, review_ui, closed_by):
    if closed_by == 'window':
        workflow.set_round_open(review_ui.h, review_ui.lead, review_ui.round, False)
    else:
        review_ui.h.results_published = True
        review_ui.h.save(update_fields=['results_published'])
    _, soup = read(client, review_ui, review_ui.judges[0], 'score')
    panel = soup.select_one('[data-score-panel]')
    assert panel is not None
    assert not panel.select('form,input,textarea,select')
    assert '已保存的个人评分（只读）' in panel.get_text()
    assert panel.get_text().count('8 / 10') == 4
    assert '已保存的团队建议' in panel.get_text()
    text = soup.get_text(' ', strip=True)
    assert ('结果已公布，本轮评分只读' if closed_by == 'published' else '本轮窗口已关闭，当前只读') in text
    assert '本轮评分窗口开放中' not in text
    _, tasks = read(client, review_ui, review_ui.judges[0], 'tasks')
    assert '已确认' in tasks.get_text()
    assert ('结果已公布，本轮评分只读' if closed_by == 'published' else '可查看已保存记录') in tasks.get_text()
    assert '并评分' not in tasks.get_text()


def test_closed_judge_forged_post_preserves_saved_revision_and_values(client, review_ui):
    workflow.set_round_open(review_ui.h, review_ui.lead, review_ui.round, False)
    client.force_login(review_ui.judges[0])
    address = endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk)
    forged = {**{key: 1 for key in review_ui.values}, 'revision': 1, 'feedback': '伪造修改', 'action': 'final'}
    response = client.post(address, forged)
    assert response.status_code == 200
    panel = BeautifulSoup(response.content, 'html.parser').select_one('[data-score-panel]')
    assert '本次输入未保存' in panel.get_text()
    assert '伪造修改' not in panel.get_text()
    assert not panel.select('form,input,textarea,select')
    review_ui.saved.refresh_from_db()
    assert review_ui.saved.revision == 1
    assert review_ui.saved.values == review_ui.values
    assert review_ui.saved.feedback == '已保存的团队建议'


def test_open_assigned_judge_keeps_editable_form_and_real_save(client, review_ui):
    _, soup = read(client, review_ui, review_ui.judges[0], 'score')
    panel = soup.select_one('[data-score-panel]')
    assert panel.select_one('form[method="post"]')
    for key in review_ui.values:
        assert panel.select_one(f'input[name="{key}"][value="8"]')
    assert panel.select_one('button[name="action"][value="draft"]')
    assert panel.select_one('button[name="action"][value="final"]')
    response = client.post(
        endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk),
        {
            **{key: 9 for key in review_ui.values},
            'revision': 1,
            'feedback': '新的正式建议',
            'action': 'final',
        },
    )
    assert response.status_code == 302
    review_ui.saved.refresh_from_db()
    assert review_ui.saved.revision == 2
    assert set(review_ui.saved.values.values()) == {9}
    assert review_ui.saved.feedback == '新的正式建议'


def test_reviewer_post_cross_track_wrong_round_and_own_team_remain_denied(client, review_ui):
    client.force_login(review_ui.reviewer)
    own_score = endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk)
    assert client.post(own_score, {**review_ui.values, 'revision': 0, 'action': 'final'}).status_code == 403
    for route in ('score', 'media-source'):
        kwargs = {'round_id': review_ui.round.pk, 'project_id': review_ui.projects[1].pk}
        if route == 'media-source':
            kwargs['kind'] = 'preliminary'
        assert client.get(endpoint(review_ui.h, route, **kwargs)).status_code == 403
    workflow.set_round_open(review_ui.h, review_ui.lead, review_ui.round, False)
    next_round = workflow.create_round(
        review_ui.h,
        review_ui.lead,
        kind='preliminary',
        name='另一个任务范围',
        rules=rules('preliminary', review_ui.lead),
        panel=[{'user_id': str(user.pk), 'tracks': [], 'nonstudent': False} for user in review_ui.judges[2:]],
        signer=review_ui.lead,
        signer_nonstudent=True,
    )
    client.force_login(review_ui.judges[0])
    assert (
        client.get(
            endpoint(review_ui.h, 'score', round_id=next_round.pk, project_id=review_ui.projects[0].pk)
        ).status_code
        == 403
    )
    Participant.objects.create(
        hackathon=review_ui.h,
        user=review_ui.judges[0],
        name='后来加入本队的评委',
        consent=True,
        team=review_ui.projects[0].team,
    )
    own_team = endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk)
    assert client.get(own_team).status_code == 403
    assert client.post(own_team, {**review_ui.values, 'revision': 1, 'action': 'final'}).status_code == 403
    assert RoundScore.objects.filter(hackathon=review_ui.h).count() == 1


def test_reviewer_without_global_judge_role_uses_read_only_workspace(client, review_ui):
    StaffAssignment.objects.filter(hackathon=review_ui.h, user=review_ui.reviewer).delete()
    for route in ('judging', 'tasks', 'score'):
        response, soup = read(client, review_ui, review_ui.reviewer, route)
        assert response.context['working_identity_key'] == 'reviewer'
        assert response.context['working_identity'] == '复核'
        assert '复核工作台' in soup.get_text()
        assert '并评分' not in soup.get_text()
    score_url = endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk)
    assert client.post(score_url, {**review_ui.values, 'revision': 0, 'action': 'final'}).status_code == 403


def test_nonorganizer_signer_uses_reviewer_workspace_and_can_read_snapshot(client, review_ui):
    for task in review_ui.round.tasks:
        project = next(project for project in review_ui.projects if str(project.pk) == task['project_id'])
        for judge in review_ui.judges:
            if (
                str(judge.pk) in task['judges']
                and not RoundScore.objects.filter(round=review_ui.round, project=project, judge=judge).exists()
            ):
                workflow.save_round_score(
                    review_ui.h, judge, review_ui.round, project, values=review_ui.values, final=True, revision=0
                )
    workflow.set_round_open(review_ui.h, review_ui.lead, review_ui.round, False)
    snapshot = workflow.make_snapshot(
        review_ui.h,
        review_ui.lead,
        review_ui.round,
        kind='finalists',
        reason='用于签认入口UI核验',
        seats={str(project.team.track_id): 1 for project in review_ui.projects},
        alternates=0,
    )
    assert not StaffAssignment.objects.filter(hackathon=review_ui.h, user=review_ui.signer).exists()
    response, _ = read(client, review_ui, review_ui.signer, 'judging')
    assert response.context['working_identity_key'] == 'reviewer'
    client.force_login(review_ui.signer)
    response = client.get(endpoint(review_ui.h, 'snapshot', snapshot_id=snapshot.pk))
    assert response.status_code == 200
    assert response.context['working_identity_key'] == 'reviewer'
    assert response.context['can_sign'] is True


def test_window_closing_during_save_renders_fresh_saved_state_read_only(client, review_ui, monkeypatch):
    from django.core.exceptions import ValidationError

    def close_while_saving(*args, **kwargs):
        workflow.set_round_open(review_ui.h, review_ui.lead, review_ui.round, False)
        raise ValidationError('本轮评分窗口已关闭，本次输入未保存。')

    monkeypatch.setattr(workflow, 'save_round_score', close_while_saving)
    client.force_login(review_ui.judges[0])
    response = client.post(
        endpoint(review_ui.h, 'score', round_id=review_ui.round.pk, project_id=review_ui.projects[0].pk),
        {
            **{key: 1 for key in review_ui.values},
            'revision': 1,
            'feedback': '未保存输入',
            'action': 'final',
        },
    )
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    panel = soup.select_one('[data-score-panel]')
    assert not panel.select('form,input,textarea,select')
    assert '本次输入未保存' in panel.get_text()
    assert '未保存输入' not in panel.get_text()
    assert panel.get_text().count('8 / 10') == 4
    assert '本轮窗口已关闭，当前只读' in soup.get_text()
    assert '本轮评分窗口开放中' not in soup.get_text()
    review_ui.saved.refresh_from_db()
    assert review_ui.saved.revision == 1
