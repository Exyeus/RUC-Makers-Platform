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
from eventyay.hackathon.models import Asset, DemoSlot, Hackathon, Score, StaffAssignment, Team
from eventyay.hackathon.scoring import aggregate, weighted_total
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
    'action': 'final',
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
    assert client.post(scoring, {**SCORE, 'scoring_revision': 1, 'action': 'draft'}).status_code == 302
    assert not Score.objects.get(project=project, judge=judge).final
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
        ([0, 0, 0], 1, Decimal('0.00'), 1),
        ([], 1, None, 0),
        ([0, 10], 1, Decimal('50.00'), 2),
        ([6], 2, Decimal('60.00'), 1),
        ([1, 2, 3, 4], 5, Decimal('25.00'), 4),
        ([0, 0, 0, 10], 2, Decimal('25.00'), 4),
        ([0, 0, 0, 10], 3, Decimal('25.00'), 4),
    ],
)
def test_trimmed_mean_drops_extremes_and_falls_back_on_thin_panels(hackathon, totals, trim, expected, counted):
    hackathon.score_aggregation, hackathon.trim_extremes = 'trimmed', trim
    scores = [Score(innovation=value, implementation=value, impact=value, presentation=value) for value in totals]
    assert aggregate(scores, hackathon) == (expected, counted)


def test_partial_draft_totals_are_not_silently_read_as_zero(hackathon):
    complete = Score(innovation=10, implementation=10, impact=10, presentation=10)
    partial = Score(innovation=0, implementation=None, impact=0, presentation=0)
    assert complete.is_complete and not partial.is_complete
    assert partial.filled == 3
    assert weighted_total(partial, hackathon) is None
    assert aggregate([complete, partial], hackathon) == (Decimal('100.00'), 1)


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


def test_thin_panel_explains_the_automatic_average(client, hackathon, organizer_user, judge, project):
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
    assert row['average'] == Decimal('79.00') and row['counted'] == 1 and row['thin_panel']
    assert row['complete'] and row['rank'] == 1
    client.force_login(organizer_user)
    body = client.get(endpoint(hackathon, 'results')).content.decode()
    assert '有效评分不足去极窗口' in body and '不足 3 份时不去极值' in body
    exported = client.get(endpoint(hackathon, 'export', kind='results')).content.decode('utf-8-sig')
    assert '采用评分数' in exported and '去掉 1 个最高' in exported


def test_missing_final_scores_still_block_publication(client, hackathon, organizer_user, judge, project):
    hackathon.score_aggregation = 'trimmed'
    hackathon.save()
    client.force_login(organizer_user)
    data = model_to_dict(hackathon, fields=forms.SettingsForm.Meta.fields)
    data.update({'starts_at': hackathon.event.date_from.isoformat(), 'ends_at': hackathon.event.date_to.isoformat()})
    response = client.post(endpoint(hackathon, 'settings'), {**data, 'results_published': True})
    assert '暂不能公布成绩' in response.content.decode()
    hackathon.refresh_from_db()
    assert not hackathon.results_published
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
    # A panel thinner than the trimming window still counts as fully reviewed, so the
    # publish gate is only about missing confirmations, never about the judge headcount.
    assert all(row['complete'] and row['counted'] == 1 for row in services.results(hackathon))


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


def test_half_finished_score_saves_as_draft_but_cannot_be_confirmed(client, hackathon, judge, project):
    client.force_login(judge)
    location = endpoint(hackathon, 'score', project_id=project.pk)
    partial = {
        'innovation': 8,
        'implementation': '',
        'impact': '',
        'presentation': '',
        'revision': 0,
        'scoring_revision': 0,
    }
    assert client.post(location, {**partial, 'action': 'draft'}).status_code == 302
    score = Score.objects.get(project=project, judge=judge)
    assert score.innovation == 8 and score.implementation is None and not score.final
    assert score.total is None and score.filled == 1 and not score.is_complete
    response = client.post(location, {**partial, 'action': 'final', 'revision': score.revision})
    assert response.status_code == 200
    assert '确认评分前请填写' in response.content.decode()
    score.refresh_from_db()
    assert not score.final and score.revision == 1
    rejected = client.post(location, {**partial, 'innovation': 11, 'action': 'final', 'revision': score.revision})
    assert 'id_innovation_error' in rejected.content.decode()
    assert '确认评分前请填写「创新性」' not in rejected.content.decode()
    assert '已填 1 项' in client.get(location).content.decode()
    assert '已填 1 项' in client.get(endpoint(hackathon, 'judging')).content.decode()
    assert not services.results(hackathon)[0]['complete']


def test_confirming_advances_to_the_next_unfinished_team(client, hackathon, judge, team, project):
    other_team = Team.objects.create(hackathon=hackathon, captain=team.captain, name='下一队', track=team.track)
    other = approved_project(hackathon, other_team, title='另一个作品')
    client.force_login(judge)
    location = endpoint(hackathon, 'score', project_id=project.pk)
    assert client.post(location, {**SCORE, 'action': 'draft'}).url == location
    draft = Score.objects.get(project=project, judge=judge)
    assert not draft.final and draft.revision == 1
    assert [entry['project'].pk for entry in client.get(endpoint(hackathon, 'judging')).context['entries']] == [
        project.pk,
        other.pk,
    ]
    assert client.post(location, {**SCORE, 'revision': draft.revision}).url == endpoint(
        hackathon, 'score', project_id=other.pk
    )
    confirmed = Score.objects.get(project=project, judge=judge)
    assert confirmed.final and confirmed.revision == 2
    body = client.get(location).content.decode()
    assert '更新已确认评分' in body and '改为草稿保存' in body and 'data-confirm' in body
    assert '下一份未完成' in body
    assert '下一份未完成' not in client.get(endpoint(hackathon, 'score', project_id=other.pk)).content.decode()
    assert client.post(location, {**SCORE, 'action': 'draft', 'revision': confirmed.revision}).status_code == 302
    confirmed.refresh_from_db()
    assert not confirmed.final


def test_score_post_without_a_button_leaves_the_confirmed_score_alone(client, hackathon, judge, project):
    client.force_login(judge)
    location = endpoint(hackathon, 'score', project_id=project.pk)
    assert client.post(location, SCORE).status_code == 302
    score = Score.objects.get(project=project, judge=judge)
    assert score.final and score.revision == 1
    response = client.post(
        location, {**{key: value for key, value in SCORE.items() if key != 'action'}, 'revision': score.revision}
    )
    assert response.status_code == 200
    assert '按钮提交' in response.content.decode()
    score.refresh_from_db()
    assert score.final and score.revision == 1


def test_judging_home_filters_narrow_the_same_task_set(client, hackathon, judge, team, project):
    other_team = Team.objects.create(hackathon=hackathon, captain=team.captain, name='另一队', track=team.track)
    other = approved_project(hackathon, other_team, title='另一个作品')
    Score.objects.create(hackathon=hackathon, project=project, judge=judge, innovation=5, final=False)
    client.force_login(judge)
    base = endpoint(hackathon, 'judging')
    body = client.get(base).content.decode()
    assert project.title in body and other.title in body and '继续未完成' in body
    assert '没有符合筛选条件的作品' in client.get(base, {'status': 'confirmed'}).content.decode()
    draft_body = client.get(base, {'status': 'draft'}).content.decode()
    assert project.title in draft_body and other.title not in draft_body
    search_body = client.get(base, {'q': other_team.name}).content.decode()
    assert other.title in search_body and project.title not in search_body
    # The tab links must carry the filters already in the URL; a doubled question mark would
    # reach the view as GET['?status'] and leave every tab showing the same list.
    assert 'href="?status=draft"' in body and '??' not in body
    combined = client.get(base, {'q': other_team.name}).content.decode()
    assert 'status=draft' in combined and 'q=' in combined and '??' not in combined


def test_organizers_see_who_owes_which_score(client, hackathon, organizer_user, judge, project):
    Score.objects.create(hackathon=hackathon, project=project, judge=judge, innovation=5, final=False)
    retired = User.objects.create_user(email='retired@example.org', is_active=False)
    StaffAssignment.objects.create(hackathon=hackathon, user=retired, role='judge')
    rows = services.review_progress(hackathon)
    assert [row['user'] for row in rows] == [judge]
    row = rows[0]
    assert row['expected'] == 1 and row['confirmed'] == 0 and row['drafts'] == 1 and not row['complete']
    assert [(item['project'], item['draft']) for item in row['outstanding']] == [(project, True)]
    client.force_login(organizer_user)
    body = client.get(endpoint(hackathon, 'results')).content.decode()
    assert '评委进度与缺评明细' in body and '现场评委' in body and '有草稿' in body
    hackathon.results_published = True
    hackathon.save()
    client.logout()
    anonymous = client.get(endpoint(hackathon, 'results')).content.decode()
    assert '评委进度与缺评明细' not in anonymous and judge.email not in anonymous


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
