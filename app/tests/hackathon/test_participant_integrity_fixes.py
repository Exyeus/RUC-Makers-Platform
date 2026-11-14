import pytest
from django.urls import reverse

from eventyay.hackathon import forms, services
from eventyay.hackathon.models import Asset, Project, SupportTicket
from tests.hackathon.test_workflows import project_data


pytestmark = pytest.mark.django_db


def endpoint(h, name, **kwargs):
    return reverse('hackathon:' + name, kwargs={'hackathon_slug': h.slug, **kwargs})


def review_payload(client, location, **changes):
    form = client.get(location).context['form']
    data = {name: form.initial[name] for name in ('version', 'review_token')}
    data.update(state='approved', equipment_checked='on', review_note='', award='')
    data.update(changes)
    return data


def test_review_is_bound_to_formal_version_and_current_review(client, hackathon, participant, team, organizer_user):
    data = project_data()
    data.pop('version')
    project = Project.objects.create(hackathon=hackathon, team=team, **data)
    Asset.objects.create(
        hackathon=hackathon, project=project, kind='poster', filename='p.png', file='p.png', size=1, checksum='a' * 64
    )
    form = forms.ProjectForm(project_data(version=project.revision), instance=project)
    assert form.is_valid()
    project = services.save_project(hackathon, participant, form, submit=True)
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'review', project_id=project.pk)
    old = review_payload(client, location)
    form = forms.ProjectForm(
        project_data(version=project.revision, description='new unsubmitted draft'), instance=project
    )
    assert form.is_valid()
    project = services.save_project(hackathon, participant, form)
    assert client.post(location, old).status_code == 200
    project.refresh_from_db()
    assert project.state == 'changes' and not project.equipment_checked
    # Even a freshly read review cannot turn the unsubmitted draft into a formal submission.
    assert client.post(location, review_payload(client, location)).status_code == 200
    project.refresh_from_db()
    assert project.state == 'changes'
    # Feedback on an unsubmitted draft must not launder its reason into formal REVIEW.
    assert (
        client.post(location, review_payload(client, location, state='changes', review_note='请补新版本')).status_code
        == 302
    )
    project.refresh_from_db()
    assert project.state_reason == 'edited'
    assert client.post(location, review_payload(client, location)).status_code == 200
    project.refresh_from_db()
    assert project.state == 'changes'
    form = forms.ProjectForm(project_data(version=project.revision, description=project.description), instance=project)
    assert form.is_valid()
    project = services.save_project(hackathon, participant, form, submit=True)
    fresh = review_payload(client, location)
    assert client.post(location, fresh).status_code == 302
    project.refresh_from_db()
    assert project.state == 'approved' and project.equipment_checked
    # Another stale organizer action cannot revert a newer review decision.
    assert client.post(location, fresh).status_code == 200


def test_ticket_old_form_rejects_new_note_but_fresh_form_can_resolve(client, hackathon, user, organizer_user):
    ticket = SupportTicket.objects.create(
        hackathon=hackathon, user=user, category='other', subject='路演时间', body='原诉求'
    )
    location = endpoint(hackathon, 'support-reply', ticket_id=ticket.pk)
    client.force_login(organizer_user)
    old = client.get(location).context['form'].initial['last_message_id']
    client.force_login(user)
    assert (
        client.post(
            endpoint(hackathon, 'support'), {'action': 'append', 'ticket': ticket.pk, 'body': '更新：必须提前离场'}
        ).status_code
        == 302
    )
    client.force_login(organizer_user)
    response = client.post(location, {'action': 'reply', 'state': 'resolved', 'body': '旧处理', 'last_message_id': old})
    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.state == 'open' and not ticket.messages.filter(from_staff=True).exists()
    fresh = client.get(location).context['form'].initial['last_message_id']
    assert (
        client.post(
            location, {'action': 'reply', 'state': 'resolved', 'body': '已核对新诉求', 'last_message_id': fresh}
        ).status_code
        == 302
    )
    ticket.refresh_from_db()
    assert ticket.state == 'resolved' and ticket.messages.count() == 2
