from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.forms.models import model_to_dict
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event, User
from eventyay.hackathon import forms, services
from eventyay.hackathon.models import Asset, DemoSlot, Hackathon, Score, StaffAssignment
from eventyay.hackathon.scoring import aggregate
from tests.hackathon.test_workflows import approved_project, endpoint


pytestmark = pytest.mark.django_db
PASSWORD = 'Judge!SecurePass2026'
DEFAULT_RULES = {
    'innovation_weight': 30,
    'implementation_weight': 40,
    'impact_weight': 20,
    'presentation_weight': 10,
    'score_aggregation': 'mean',
    'trim_extremes': 1,
}
SCORE = {
    'innovation': 9,
    'implementation': 8,
    'impact': 7,
    'presentation': 6,
    'final': True,
    'revision': 0,
    'scoring_revision': 0,
}


@pytest.fixture
def judge(hackathon):
    user = User.objects.create_user(email='livejudge@example.org', password=PASSWORD, fullname='现场评委')
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='judge')
    return user


@pytest.fixture
def project(hackathon, team):
    return approved_project(hackathon, team)


@pytest.fixture(autouse=True)
def local_cache(settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()


def test_dedicated_judge_login_and_role_guard(client, hackathon, judge, user):
    login = endpoint(hackathon, 'judge-login')
    assert '评委登录' in client.get(login).content.decode()
    assert client.get(endpoint(hackathon, 'judge-live')).url.startswith(login)
    response = client.post(login, {'email': user.email, 'password': 'a-strong-test-password'})
    assert '尚未获授' in response.content.decode()
    assert '_auth_user_id' not in client.session
    response = client.post(login, {'email': judge.email, 'password': PASSWORD})
    assert response.url == endpoint(hackathon, 'judge-live')
    assert client.get(response.url).status_code == 200


def test_organizer_can_create_judge_without_admin_privileges(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    data = {'name': '新评委', 'email': 'newjudge@example.org', 'password': PASSWORD, 'password_confirm': PASSWORD}
    response = client.post(endpoint(hackathon, 'judge-new'), data)
    assert response.url == endpoint(hackathon, 'staff')
    user = User.objects.get(email=data['email'])
    assert user.check_password(PASSWORD)
    assert not user.is_administrator and not user.is_superuser
    assert StaffAssignment.objects.filter(hackathon=hackathon, user=user, role='judge').exists()
    assert client.post(endpoint(hackathon, 'judge-new'), data).status_code == 200
    assert User.objects.filter(email=data['email']).count() == 1
    client.force_login(user)
    assert client.get(endpoint(hackathon, 'judge-new')).status_code == 403
    assert client.get(endpoint(hackathon, 'scoring-rules')).status_code == 403


def test_live_follows_current_but_score_post_stays_bound_to_original_team(client, hackathon, judge, project):
    slot = DemoSlot.objects.create(hackathon=hackathon, project=project, position=1, state='live')
    client.force_login(judge)
    score_url = endpoint(hackathon, 'score', project_id=project.pk)
    assert client.get(endpoint(hackathon, 'judge-live')).url == score_url + '?follow=1'
    current = client.get(endpoint(hackathon, 'judge-state')).json()['current']
    assert current['id'] == str(project.pk) and current['can_score']
    assert current['score_url'] == score_url + '?follow=1'
    slot.state = 'done'
    slot.save()
    assert client.get(endpoint(hackathon, 'judge-state')).json()['current'] is None
    response = client.post(score_url + '?follow=1', SCORE)
    assert response.url == endpoint(hackathon, 'judge-live')
    assert Score.objects.get(judge=judge).project_id == project.pk


def test_score_page_shows_materials_and_custom_weights(client, hackathon, judge, project):
    hackathon.innovation_weight, hackathon.implementation_weight = 40, 30
    hackathon.save()
    poster = Asset.objects.create(
        hackathon=hackathon,
        project=project,
        kind='poster',
        filename='poster.png',
        file='test/poster.png',
        size=123,
        checksum='a' * 64,
    )
    client.force_login(judge)
    response = client.get(endpoint(hackathon, 'score', project_id=project.pk))
    assert response.status_code == 200
    body = response.content.decode()
    assert project.description in body
    assert endpoint(hackathon, 'download', asset_id=poster.pk) in body
    assert 'data-weight="40"' in body and 'data-follow="false"' in body
    assert 'name="revision"' in body and 'name="scoring_revision"' in body
    assert 'hackathon/score.html' in [template.name for template in response.templates]


def test_conflicted_judge_cannot_score_current_team(client, hackathon, user, project):
    StaffAssignment.objects.create(hackathon=hackathon, user=user, role='judge')
    DemoSlot.objects.create(hackathon=hackathon, project=project, position=1, state='live')
    client.force_login(user)
    current = client.get(endpoint(hackathon, 'judge-state')).json()['current']
    assert not current['can_score'] and current['score_url'] is None
    assert client.get(endpoint(hackathon, 'judge-live')).status_code == 200
    assert client.post(endpoint(hackathon, 'score', project_id=project.pk), SCORE).status_code == 403


def test_repeated_and_stale_submission_cannot_overwrite_saved_score(client, hackathon, judge, project):
    client.force_login(judge)
    location = endpoint(hackathon, 'score', project_id=project.pk)
    assert client.post(location, SCORE).status_code == 302
    response = client.post(location, {**SCORE, 'innovation': 1})
    assert response.status_code == 200
    assert '已在其他页面保存' in response.content.decode()
    assert Score.objects.get(project=project, judge=judge).innovation == 9
    assert client.post(location, {**SCORE, 'innovation': 8, 'revision': 1}).status_code == 302
    assert Score.objects.get(project=project, judge=judge).revision == 2


def test_old_rule_revision_rejected_and_rules_locked_after_first_draft(
    client, hackathon, organizer_user, judge, project
):
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'scoring-rules')
    assert (
        client.post(location, {**DEFAULT_RULES, 'innovation_weight': 40, 'implementation_weight': 30}).status_code
        == 302
    )
    hackathon.refresh_from_db()
    assert hackathon.scoring_revision == 1
    client.force_login(judge)
    scoring = endpoint(hackathon, 'score', project_id=project.pk)
    response = client.post(scoring, SCORE)
    assert '规则已更新' in response.content.decode()
    assert not Score.objects.filter(project=project).exists()
    assert client.post(scoring, {**SCORE, 'scoring_revision': 1, 'final': False}).status_code == 302
    client.force_login(organizer_user)
    response = client.post(location, DEFAULT_RULES)
    assert '规则已锁定' in response.content.decode()
    hackathon.refresh_from_db()
    assert hackathon.innovation_weight == 40


def test_rules_validate_sum_and_support_defaults(client, hackathon, organizer_user):
    client.force_login(organizer_user)
    location = endpoint(hackathon, 'scoring-rules')
    assert '必须为 100%' in client.post(location, {**DEFAULT_RULES, 'innovation_weight': 90}).content.decode()
    assert (
        client.post(location, {**DEFAULT_RULES, 'score_aggregation': 'trimmed', 'trim_extremes': 0}).status_code == 200
    )
    assert (
        client.post(
            location,
            {
                **DEFAULT_RULES,
                'innovation_weight': 100,
                'implementation_weight': 0,
                'impact_weight': 0,
                'presentation_weight': 0,
            },
        ).status_code
        == 302
    )
    assert client.post(location, {'action': 'defaults'}).status_code == 302
    hackathon.refresh_from_db()
    assert hackathon.innovation_weight == 30 and hackathon.score_aggregation == 'mean'


@pytest.mark.parametrize(
    'totals, trim, expected, counted',
    [
        ([1, 4, 5, 6, 10], 1, Decimal('50.00'), 3),
        ([8, 8, 8, 8, 8], 1, Decimal('80.00'), 3),
        ([0, 2, 5, 8, 10], 2, Decimal('50.00'), 1),
        ([0, 10], 1, None, 0),
        ([0, 0, 0], 1, Decimal('0.00'), 1),
    ],
)
def test_trimmed_mean_removes_exact_counts_even_with_ties(hackathon, totals, trim, expected, counted):
    hackathon.score_aggregation, hackathon.trim_extremes = 'trimmed', trim
    scores = [Score(innovation=value, implementation=value, impact=value, presentation=value) for value in totals]
    assert aggregate(scores, hackathon) == (expected, counted)


def test_default_and_custom_weighted_totals(hackathon):
    score = Score(hackathon=hackathon, innovation=9, implementation=8, impact=7, presentation=6)
    assert score.total == 79
    hackathon.innovation_weight, hackathon.implementation_weight = 40, 30
    assert score.total == 80
    assert aggregate([score], hackathon) == (Decimal('80.00'), 1)


def test_trimmed_results_preserve_nominations_and_exclude_drafts_inactive_judges(hackathon, project):
    hackathon.score_aggregation = 'trimmed'
    hackathon.save()
    for index, value in enumerate([1, 4, 5, 6, 10]):
        judge = User.objects.create_user(email=f'trim{index}@example.org')
        StaffAssignment.objects.create(hackathon=hackathon, user=judge, role='judge')
        Score.objects.create(
            hackathon=hackathon,
            project=project,
            judge=judge,
            innovation=value,
            implementation=value,
            impact=value,
            presentation=value,
            final=True,
            creativity=True,
        )
    disabled = User.objects.create_user(email='disabled@example.org', is_active=False)
    StaffAssignment.objects.create(hackathon=hackathon, user=disabled, role='judge')
    Score.objects.create(
        hackathon=hackathon,
        project=project,
        judge=disabled,
        innovation=0,
        implementation=0,
        impact=0,
        presentation=0,
        final=True,
    )
    row = services.results(hackathon)[0]
    assert row['average'] == 50 and row['counted'] == 3 and row['trimmed'] == 2
    assert row['creativity'] == 5 and row['complete'] and row['expected'] == 5
    Score.objects.filter(judge__email='trim0@example.org').update(final=False)
    assert not services.results(hackathon)[0]['complete']


def test_too_few_scores_explains_missing_result(client, hackathon, organizer_user, judge, project):
    hackathon.score_aggregation = 'trimmed'
    hackathon.save()
    Score.objects.create(
        hackathon=hackathon,
        project=project,
        judge=judge,
        innovation=9,
        implementation=8,
        impact=7,
        presentation=6,
        final=True,
    )
    row = services.results(hackathon)[0]
    assert row['average'] is None and not row['complete'] and 'rank' not in row
    client.force_login(organizer_user)
    assert '有效人数不足' in client.get(endpoint(hackathon, 'results')).content.decode()
    exported = client.get(endpoint(hackathon, 'export', kind='results')).content.decode('utf-8-sig')
    assert '采用评分数' in exported and '去掉 1 个最高' in exported


def test_insufficient_trimmed_scores_cannot_be_published(client, hackathon, organizer_user, judge, project):
    hackathon.score_aggregation = 'trimmed'
    hackathon.save()
    Score.objects.create(
        hackathon=hackathon,
        project=project,
        judge=judge,
        innovation=9,
        implementation=8,
        impact=7,
        presentation=6,
        final=True,
    )
    client.force_login(organizer_user)
    data = model_to_dict(hackathon, fields=forms.SettingsForm.Meta.fields)
    response = client.post(endpoint(hackathon, 'settings'), {**data, 'results_published': True})
    assert response.status_code == 200
    hackathon.refresh_from_db()
    assert not hackathon.results_published


def test_judge_role_and_scoring_are_isolated_between_events(client, hackathon, judge, project):
    event = Event.objects.create(
        organizer=hackathon.event.organizer, name='Other', slug='other-judge', date_from=timezone.now(), currency='CNY'
    )
    other = Hackathon.objects.create(
        event=event,
        slug='other-judge',
        name='Other',
        is_public=True,
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    client.force_login(judge)
    assert client.get(endpoint(other, 'judge-state')).status_code == 403
    assert client.post(endpoint(other, 'score', project_id=project.pk), SCORE).status_code == 403
    with scope(event=event):
        StaffAssignment.objects.create(hackathon=other, user=judge, role='judge')
    assert client.get(endpoint(other, 'judge-state')).json()['current'] is None
    assert client.post(endpoint(other, 'score', project_id=project.pk), SCORE).status_code == 404
    assert not Score.objects.filter(project=project).exists()


def test_score_is_private_to_its_judge_and_locked_after_publication(client, hackathon, judge, project):
    another = User.objects.create_user(email='another-judge@example.org')
    StaffAssignment.objects.create(hackathon=hackathon, user=another, role='judge')
    Score.objects.create(
        hackathon=hackathon,
        project=project,
        judge=another,
        innovation=9,
        implementation=8,
        impact=7,
        presentation=6,
        feedback='PRIVATE-JUDGE-NOTE',
        final=True,
    )
    client.force_login(judge)
    location = endpoint(hackathon, 'score', project_id=project.pk)
    assert 'PRIVATE-JUDGE-NOTE' not in client.get(location).content.decode()
    hackathon.results_published = True
    hackathon.save()
    response = client.post(location, SCORE)
    assert '锁定' in response.content.decode()
    assert not Score.objects.filter(judge=judge).exists()


@pytest.fixture
def scoring_transactional_cache(monkeypatch):
    # Keep unrelated video cache hooks out of this real PostgreSQL lock test.
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_version_sync', lambda self: None)
    monkeypatch.setattr('eventyay.base.models.cache.VersionedModel._set_cache_deleted_sync', lambda self: None)


@pytest.mark.usefixtures('scoring_transactional_cache')
@pytest.mark.django_db(transaction=True, available_apps=['eventyay.base', 'eventyay.hackathon'])
def test_concurrent_first_submissions_create_only_one_score(hackathon, judge, project):
    ready = Barrier(2)

    def submit(value):
        close_old_connections()
        try:
            with scope(event=hackathon.event):
                form = forms.ScoreForm({**SCORE, 'innovation': value}, hackathon=hackathon)
                assert form.is_valid()
                ready.wait(timeout=10)
                try:
                    services.save_score(hackathon, judge, project, form)
                except ValidationError:
                    return 'stale'
                return 'saved'
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, [7, 9]))
    assert sorted(outcomes) == ['saved', 'stale']
    assert Score.objects.filter(project=project, judge=judge).count() == 1
    assert Score.objects.get(project=project, judge=judge).revision == 1
