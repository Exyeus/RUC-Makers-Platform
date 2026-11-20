"""This edition's submission: one preliminary MP4 and one roadshow video or PDF, nothing else forced."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from eventyay.hackathon import services
from eventyay.hackathon.forms import AssetForm, ProjectForm, UploadStartForm
from eventyay.hackathon.models import Asset, MaterialPolicy, Project
from eventyay.hackathon.uploads import install_asset


pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(hackathon):
    return MaterialPolicy.objects.create(hackathon=hackathon, enabled=True)


def asset(project, kind, name, probe=None):
    return Asset.objects.create(
        hackathon=project.hackathon,
        project=project,
        kind=kind,
        file=f'synthetic/{name}',
        filename=name,
        original_filename=name,
        size=3,
        checksum=kind[0] * 64,
        media_probe=probe or {},
    )


def test_only_name_description_track_and_two_materials_are_required(policy, hackathon, team):
    project = Project.objects.create(hackathon=hackathon, team=team, title='作品', summary='一句话说明')
    errors = services.submission_errors(project)
    assert any('初评' in error for error in errors)
    assert any('上场' in error or '路演' in error for error in errors)
    for legacy in ('海报', '代码仓库', '自查', '备用演示', '实机演示步骤'):
        assert not any(legacy in error for error in errors), legacy
    asset(project, 'preliminary', 'a.mp4', {'state': 'verified', 'duration_seconds': 58.0})
    asset(project, 'slides', 'roadshow.pdf')
    project.final_material_kind = 'slides'
    project.save()
    assert services.submission_errors(project) == []


def test_preliminary_is_mp4_only_and_roadshow_slides_must_be_pdf(policy, hackathon):
    start = UploadStartForm(
        {'id': '6a1c0a7e-0c47-4d8c-9d5c-3b3a7c1f0000', 'kind': 'preliminary', 'filename': 'clip.webm', 'size': 10},
        hackathon=hackathon,
    )
    assert not start.is_valid()
    pptx = UploadStartForm(
        {'id': '6a1c0a7e-0c47-4d8c-9d5c-3b3a7c1f0001', 'kind': 'slides', 'filename': 'deck.pptx', 'size': 10},
        hackathon=hackathon,
    )
    assert not pptx.is_valid() and 'PDF' in str(pptx.errors)
    form = AssetForm(
        {'kind': 'slides'}, {'file': SimpleUploadedFile('deck.pptx', b'PK\x03\x04data')}, hackathon=hackathon
    )
    assert not form.is_valid() and 'PDF' in str(form.errors)
    choices = [value for value, _label in AssetForm(hackathon=hackathon).fields['kind'].choices if value]
    assert choices == ['preliminary', 'video', 'slides']


def test_latest_roadshow_upload_becomes_the_chosen_form(policy, hackathon, team, user):
    project = Project.objects.create(hackathon=hackathon, team=team, title='作品', summary='说明')
    install_asset(hackathon, user, project, 'video', 'roadshow.mp4', 3, 'v' * 64, 'synthetic/roadshow.mp4')
    project.refresh_from_db()
    assert project.final_material_kind == 'video'
    install_asset(hackathon, user, project, 'slides', 'roadshow.pdf', 3, 's' * 64, 'synthetic/roadshow.pdf')
    project.refresh_from_db()
    assert project.final_material_kind == 'slides'


def test_project_form_drops_the_manual_roadshow_switch(policy, hackathon):
    assert 'final_material_kind' not in ProjectForm(hackathon=hackathon).fields
    assert 'final_material_kind' in ProjectForm().fields
