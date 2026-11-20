"""Submission reading order and the server-rendered receipt contract."""

import uuid
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.utils import timezone

from eventyay.hackathon.models import Project, UploadSession
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def project_page(client, hackathon, team, user, **changes):
    item = Project.objects.create(hackathon=hackathon, team=team, title='当前作品', **changes)
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'project'))
    assert response.status_code == 200
    return item, BeautifulSoup(response.content, 'html.parser')


def test_submission_controls_follow_materials_and_belong_to_project_form(client, hackathon, team, user):
    _, soup = project_page(client, hackathon, team, user)
    sections = soup.select('.project-workflow > section')
    assert [section.select_one('h2').get('id') for section in sections] == [
        'project-information-title',
        'project-materials-title',
        'project-submit-title',
    ]
    project_form = soup.select_one('[data-project-form]')
    assert not project_form.select('form')
    assert project_form.select_one('button[name=action]')['value'] == 'save'
    for name in ('checklist', 'public_consent'):
        fields = soup.select(f'input[name="{name}"]')
        assert len(fields) == 1
        assert fields[0]['form'] == project_form['id']
        assert fields[0].find_parent('section') == sections[2]
    submit = soup.select_one('[data-submit-project]')
    assert submit['form'] == project_form['id']
    assert '正式提交当前版本' in submit.text
    assert '正式提交 / 排队登记' not in soup.text
    assert soup.select_one('[data-project-status]').find_parent('section') == sections[0]
    assert soup.select_one('[data-submit-status]').find_parent('section') == sections[2]
    assert soup.select_one('[data-submit-status]')['role'] == 'status'


def test_current_receipt_is_single_and_history_starts_collapsed(client, hackathon, team, user):
    receipt = uuid.uuid4()
    _, soup = project_page(
        client,
        hackathon,
        team,
        user,
        state='submitted',
        revision=8,
        submission_receipt=receipt,
        submission_intent_at=timezone.now(),
        submitted_at=timezone.now() - timedelta(hours=1),
    )
    assert len(soup.select('[data-submission-state]')) == 1
    assert len(soup.select('[data-receipt-id]')) == 1
    assert str(receipt) == soup.select_one('[data-receipt-id]').text.strip()
    assert soup.text.count(str(receipt)) == 1
    assert 'open' not in soup.select_one('.project-history').attrs
    assert soup.select_one('[data-receipt-id]').find_parent('details') is not None
    assert 'open' not in soup.select_one('[data-receipt-id]').find_parent('details').attrs


def test_active_upload_is_visible_and_names_queued_action(client, hackathon, team, user):
    item = Project.objects.create(hackathon=hackathon, team=team, title='正在上传')
    UploadSession.objects.create(
        hackathon=hackathon,
        project=item,
        user=user,
        kind='poster',
        original_filename='poster.png',
        size=100,
        expires_at=timezone.now() + timedelta(hours=1),
        deadline_at=hackathon.submission_deadline,
    )
    client.force_login(user)
    soup = BeautifulSoup(client.get(endpoint(hackathon, 'project')).content, 'html.parser')
    assert 'open' in soup.select_one('.project-history').attrs
    assert '登记排队提交' in soup.select_one('[data-submit-project]').text
    assert soup.select_one('[data-cancel-upload]') is not None


def test_queued_or_closed_submission_controls_stay_disabled(client, hackathon, team, user):
    item, soup = project_page(client, hackathon, team, user, state='queued')
    assert 'disabled' in soup.select_one('[data-submit-project]').attrs
    assert 'disabled' in soup.select_one('[data-project-form] button[name=action]').attrs
    item.state = 'draft'
    item.save()
    hackathon.submission_deadline = timezone.now() - timedelta(seconds=1)
    hackathon.save()
    soup = BeautifulSoup(client.get(endpoint(hackathon, 'project')).content, 'html.parser')
    assert soup.select_one('[data-project-form]')['data-submissions-open'] == 'false'
    assert 'disabled' in soup.select_one('[data-submit-project]').attrs
