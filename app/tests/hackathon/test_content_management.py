from datetime import timedelta

import pytest
from django.utils import timezone

from eventyay.hackathon import forms, matching_forms
from eventyay.hackathon.models import AuditEntry, BonusRule, ResourceRead, Track
from eventyay.hackathon.views import CONTENT
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db

CONTENT_DATA = {
    'announcement': {'title': '参赛通知', 'body': '请准时到场。', 'audience': 'all'},
    'resource': {
        'title': '开发指南',
        'category': 'guide',
        'summary': '开始开发',
        'body': '准备开发环境。',
        'minutes': 5,
        'position': 0,
    },
    'schedule': {
        'title': '开幕环节',
        'kind': '活动',
        'starts_at': '2026-10-01T09:00',
        'ends_at': '2026-10-01T10:00',
        'location': '报告厅',
    },
    'track': {'name': '开放创新赛道', 'description': '发挥创意。'},
}

LIVE_KINDS = ('announcement', 'resource', 'schedule', 'track')

BONUS = {'title': '开源贡献', 'description': '提交可复核的仓库记录', 'points': 5, 'active': True, 'position': 0}


def label_field(kind):
    return 'name' if kind == 'track' else 'title'


def create_content(client, hackathon, kind, action, **changes):
    model = CONTENT[kind][0]
    before = set(model.objects.values_list('pk', flat=True))
    response = client.post(
        endpoint(hackathon, 'content-new', kind=kind), {**CONTENT_DATA[kind], **changes, 'action': action}, follow=True
    )
    assert response.redirect_chain == [(endpoint(hackathon, 'content'), 302)]
    return response, model.objects.exclude(pk__in=before).get()


def save_item(client, hackathon, item, action, **changes):
    kind = next(key for key, value in CONTENT.items() if value[0] is type(item))
    data = {**CONTENT_DATA[kind], **changes, 'action': action}
    return client.post(endpoint(hackathon, 'content-edit', kind=kind, item_id=item.pk), data, follow=True)


def current_window():
    start = timezone.now().replace(second=0, microsecond=0)
    return {
        'starts_at': start.strftime('%Y-%m-%dT%H:%M'),
        'ends_at': (start + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
    }


@pytest.mark.parametrize('kind', LIVE_KINDS)
def test_only_the_button_clicked_changes_the_row(client, hackathon, organizer_user, kind):
    client.force_login(organizer_user)
    label = label_field(kind)
    _, item = create_content(client, hackathon, kind, 'draft')
    assert (item.state, item.published_at) == ('draft', None)

    renamed = CONTENT_DATA[kind][label] + '（改写）'
    response = save_item(client, hackathon, item, 'keep', **{label: renamed})
    assert '没有收到明确的保存方式，本次未保存任何内容。' in response.content.decode()
    item.refresh_from_db()
    assert getattr(item, label) == CONTENT_DATA[kind][label]

    response = save_item(client, hackathon, item, 'publish', **{label: renamed})
    assert f'{CONTENT[kind][2]}已发布，选手现在可以看到。' in response.content.decode()
    item.refresh_from_db()
    assert (getattr(item, label), item.state) == (renamed, 'published')
    assert item.published_at is not None
    published_at = item.published_at

    save_item(client, hackathon, item, 'draft')
    item.refresh_from_db()
    assert (item.state, item.published_at) == ('draft', published_at)

    response = save_item(client, hackathon, item, 'archive')
    assert '已归档，记录保留但不再对选手公开。' in response.content.decode()
    item.refresh_from_db()
    assert item.state == 'archived'
    assert CONTENT[kind][0].published(hackathon).count() == 0


@pytest.mark.parametrize('kind', LIVE_KINDS)
def test_drafting_a_published_row_asks_before_unpublishing(client, hackathon, organizer_user, kind):
    client.force_login(organizer_user)
    _, item = create_content(client, hackathon, kind, 'publish')
    page_text = client.get(endpoint(hackathon, 'content-edit', kind=kind, item_id=item.pk)).content.decode()
    assert '存草稿会立刻把它下架，确认？' in page_text

    create_content(client, hackathon, kind, 'draft')
    page_text = client.get(endpoint(hackathon, 'content-new', kind=kind)).content.decode()
    assert '存草稿会立刻把它下架' not in page_text


def test_hub_shows_state_tags_draft_counts_and_the_publish_entry(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    _, draft = create_content(client, hackathon, 'announcement', 'draft', title='草稿通知')
    create_content(client, hackathon, 'announcement', 'publish', title='公开通知')

    response = client.get(endpoint(hackathon, 'content'))
    page_text = response.content.decode()
    assert '1 条草稿' in page_text
    assert ('草稿通知' in page_text, '公开通知' in page_text) == (True, True)
    assert endpoint(hackathon, 'content-preview', kind='announcement', item_id=draft.pk) in page_text
    assert endpoint(hackathon, 'publish') in page_text


@pytest.mark.parametrize('kind,route', [('announcement', 'announcements'), ('schedule', 'schedule')])
def test_drafts_stay_off_contestant_lists_until_published(client, hackathon, organizer_user, user, kind, route):
    client.force_login(organizer_user)
    changes = current_window() if kind == 'schedule' else {}
    _, item = create_content(client, hackathon, kind, 'draft', **changes)
    client.force_login(user)
    assert item.title not in client.get(endpoint(hackathon, route)).content.decode()

    client.force_login(organizer_user)
    save_item(client, hackathon, item, 'publish')
    client.force_login(user)
    assert item.title in client.get(endpoint(hackathon, route)).content.decode()


def test_resource_detail_is_hidden_until_published_then_reads_are_recorded(client, hackathon, organizer_user, user):
    client.force_login(organizer_user)
    _, item = create_content(client, hackathon, 'resource', 'draft')
    location = endpoint(hackathon, 'resource', resource_id=item.pk)
    client.force_login(user)
    assert client.get(location).status_code == 404
    assert item.title not in client.get(endpoint(hackathon, 'resources')).content.decode()

    client.force_login(organizer_user)
    save_item(client, hackathon, item, 'publish')
    client.force_login(user)
    assert item.title in client.get(endpoint(hackathon, 'resources')).content.decode()
    assert client.post(location).status_code == 302
    assert ResourceRead.objects.filter(hackathon=hackathon, resource=item, user=user).exists()


def test_archived_track_leaves_new_selections_but_keeps_existing_teams(client, hackathon, team, user, organizer_user):
    track = team.track
    client.force_login(organizer_user)
    save_item(client, hackathon, track, 'archive')
    track.refresh_from_db()
    assert track.state == 'archived'
    assert Track.published(hackathon).count() == 0

    client.force_login(user)
    assert track.name in client.get(endpoint(hackathon, 'team-edit')).content.decode()
    assert list(forms.TeamForm(hackathon=hackathon, instance=team).fields['track'].queryset) == [track]
    assert not list(forms.TeamForm(hackathon=hackathon).fields['track'].queryset)


def test_draft_tracks_are_not_offered_for_teams_or_proposals(hackathon):
    published = Track.objects.create(hackathon=hackathon, name='已开放赛道', state='published')
    Track.objects.create(hackathon=hackathon, name='草稿赛道')

    for form in (forms.TeamForm(hackathon=hackathon), matching_forms.ProposalForm(hackathon=hackathon)):
        assert list(form.fields['track'].queryset) == [published]


@pytest.mark.parametrize('kind', ('announcement', 'resource', 'schedule'))
def test_preview_shows_the_real_page_without_publishing_or_writing(client, hackathon, organizer_user, user, kind):
    client.force_login(organizer_user)
    changes = current_window() if kind == 'schedule' else {}
    _, item = create_content(client, hackathon, kind, 'draft', **changes)
    audits = AuditEntry.objects.filter(hackathon=hackathon).count()
    location = endpoint(hackathon, 'content-preview', kind=kind, item_id=item.pk)

    response = client.get(location)
    assert response.status_code == 200
    assert response.context['preview'] is True
    assert response.context['is_organizer'] is False
    assert item.title in response.content.decode()
    if kind == 'resource':
        assert '预览页面不会记录阅读进度' in response.content.decode()
        assert '<form' not in response.content.decode().split('<article')[1]

    item.refresh_from_db()
    assert (item.state, item.published_at) == ('draft', None)
    assert ResourceRead.objects.count() == 0
    assert AuditEntry.objects.filter(hackathon=hackathon).count() == audits
    assert client.post(location, {'action': 'publish'}).status_code == 405

    client.force_login(user)
    assert client.get(location).status_code == 403


def test_staff_only_announcement_preview_says_it_never_reaches_contestants(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    _, item = create_content(client, hackathon, 'announcement', 'draft', audience='staff')

    page = client.get(
        endpoint(hackathon, 'content-preview', kind='announcement', item_id=item.pk)
    ).content.decode()
    assert '只发给工作人员' in page


def test_bonus_rules_keep_one_plain_save_button(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'content-new', kind='bonus')
    form_page = client.get(location).content.decode()
    assert 'value="save"' in form_page
    assert '发布 / 更新公开版' not in form_page

    response = client.post(location, BONUS, follow=True)
    assert '没有收到明确的保存方式' in response.content.decode()
    assert not BonusRule.objects.exists()

    response = client.post(location, {**BONUS, 'action': 'save'}, follow=True)
    assert '加分规则已保存。' in response.content.decode()
    rule = BonusRule.objects.get()
    assert (rule.title, rule.active) == ('开源贡献', True)
