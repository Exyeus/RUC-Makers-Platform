"""Role workbenches and policy-specific copy through real rendered endpoints."""

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse

from eventyay.base.models import User
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import Asset, MaterialPolicy, Project, StaffAssignment
from tests.hackathon.test_judging_workflow import capture, fill, make_judges, make_projects, rules
from tests.hackathon.test_workflows import endpoint, project_data


pytestmark = pytest.mark.django_db


def route(hackathon, name, **kwargs):
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': hackathon.slug, **kwargs})


@pytest.mark.parametrize('name', ['workflow-manage', 'material-policy', 'judge-batch'])
def test_organizer_workflow_keeps_workbench_and_active_navigation(client, hackathon, organizer_user, name):
    client.force_login(organizer_user)
    response = client.get(route(hackathon, name))
    assert response.status_code == 200
    assert response.context['working_identity_key'] == 'organizer'
    soup = BeautifulSoup(response.content, 'html.parser')
    assert soup.select_one('#sidebar [aria-current="page"]')
    workflow_link = soup.select_one('#sidebar').find('a', href=route(hackathon, 'workflow-manage'))
    assert workflow_link and workflow_link.get_text(strip=True) == '两轮评审与签认'
    assert '活动总控' in soup.select_one('#sidebar').text


def test_frozen_reviewer_and_signer_need_no_global_judge_role_for_workbench(
    client,
    hackathon,
    organizer_user,
    user,
):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    lead = User.objects.create_user(email='readonly-lead-ui@example.invalid')
    item = workflow.create_round(
        hackathon,
        organizer_user,
        kind='preliminary',
        name='只读身份验收',
        rules=rules('preliminary', lead),
        panel=[{'user_id': str(judge.pk), 'tracks': [], 'nonstudent': False} for judge in judges],
        signer=lead,
        signer_nonstudent=True,
    )
    assert not StaffAssignment.objects.filter(hackathon=hackathon, user=lead).exists()
    client.force_login(lead)
    for name, kwargs in [
        ('workflow-judging', {}),
        ('workflow-tasks', {'round_id': item.pk}),
        ('workflow-score', {'round_id': item.pk, 'project_id': projects[0].pk}),
    ]:
        response = client.get(route(hackathon, name, **kwargs))
        assert response.status_code == 200
        assert response.context['working_identity_key'] == 'reviewer'
        soup = BeautifulSoup(response.content, 'html.parser')
        assert soup.select_one('#sidebar [aria-current="page"]').get('href') == route(hackathon, 'workflow-judging')
        assert response.context['working_identity'] == '复核'
    workflow.set_round_open(hackathon, organizer_user, item, True)
    fill(hackathon, item, judges, projects)
    workflow.set_round_open(hackathon, organizer_user, item, False)
    snapshot = workflow.make_snapshot(
        hackathon,
        organizer_user,
        item,
        kind='finalists',
        reason='核对只读签认人身份',
        seats={str(projects[0].team.track_id): 1},
    )
    response = client.get(route(hackathon, 'workflow-snapshot', snapshot_id=snapshot.pk))
    assert response.status_code == 200
    assert response.context['working_identity_key'] == 'reviewer'
    assert b'workspace_identity' not in response.content
    client.force_login(user)
    assert client.get(route(hackathon, 'workflow-tasks', round_id=item.pk)).status_code == 403
    assert client.get(route(hackathon, 'material-policy') + '?workspace_identity=organizer').status_code == 403


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('kind', ['video', 'legacy_video'])
def test_material_use_name_matches_selector_saved_asset_and_status(client, hackathon, team, user, enabled, kind):
    MaterialPolicy.objects.create(hackathon=hackathon, enabled=enabled)
    data = project_data()
    data.pop('version')
    item = Project.objects.create(hackathon=hackathon, team=team, **data)
    asset = Asset.objects.create(
        hackathon=hackathon,
        project=item,
        kind=kind,
        file='synthetic/presentation.mp4',
        filename='presentation.mp4',
        original_filename='presentation.mp4',
        size=3,
        checksum='b' * 64,
    )
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'project'))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    video_label = '路演材料 · 视频（≤ 3 分钟）' if enabled else '备用录屏'
    label = video_label if kind == 'video' else kind
    assert soup.select_one('[data-upload-form] option[value="video"]').text == video_label
    assert f'{label} · presentation.mp4' in soup.select_one('[data-asset-list]').text
    status = client.get(endpoint(hackathon, 'project-status')).json()
    material = status['materials'][0]
    assert material['kind'] == label
    assert material['asset_id'] == str(asset.pk)
    assert material['kind_key'] == kind
    assert material['checksum'] == asset.checksum
    assert material['media_source_url'] == (
        route(hackathon, 'media-source', asset_id=asset.pk) if kind == 'video' else None
    )
    assert material['media_error_url'] == route(hackathon, 'support')
    assert material['media_probe'] == asset.media_probe
    asset.refresh_from_db()
    assert asset.kind == kind


@pytest.mark.parametrize('enabled,seconds', [(True, 240), (True, 120), (False, 120)])
def test_timing_copy_respects_enabled_event_and_configured_total(
    client,
    hackathon,
    organizer_user,
    enabled,
    seconds,
):
    MaterialPolicy.objects.create(hackathon=hackathon, enabled=enabled)
    hackathon.demo_seconds = seconds
    hackathon.save(update_fields=['demo_seconds'])
    client.force_login(organizer_user)
    for name in ('stage', 'manage-stage'):
        response = client.get(endpoint(hackathon, name))
        assert response.status_code == 200
        text = BeautifulSoup(response.content, 'html.parser').select_one('.stage-rule').get_text(' ', strip=True)
        if enabled:
            assert '答问' in text
            assert '不设现场 Q&A' not in text
            assert str(seconds) in text
            if seconds == 240:
                assert '3 分钟演示 + 1 分钟答问' in text
        else:
            assert '不设现场 Q&A' in text
