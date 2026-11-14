import pytest
from django.urls import reverse

from eventyay.hackathon import match_core as core
from eventyay.hackathon import matching, services
from eventyay.hackathon.forms import TeamForm
from eventyay.hackathon.matching_forms import ProfileForm, profile_initial
from eventyay.hackathon.models import MatchingProfile, RecommendationRun, Team, Track
from tests.hackathon.test_matching import market as market
from tests.hackathon.test_matching import player


def test_unknown_skills_cannot_be_inferred_as_team_gaps():
    known = core.Profile('a', 'e', skills={'ai': 1})
    private = core.Profile('b', 'e', skills=None)
    idea = core.Idea('i', 'e', frozenset(), {'ai': 1})
    assert core.marginal_coverage([private], known, idea) is None
    assert core.marginal_coverage([known], private, idea) is None
    assert core.team_coverage([private, known], idea) is None


@pytest.mark.django_db
def test_sparse_perfect_score_does_not_displace_supported_match(market):
    a, b, c = [player(market, i) for i in range(3)]
    MatchingProfile.objects.filter(participant=b).update(matching_fields=['goal'])
    MatchingProfile.objects.filter(participant=c).update(goal='explore')
    result = matching.recommendations(market, a, limit=1)['results']
    assert result[0]['target_id'] == str(c.pk)


@pytest.mark.django_db
def test_disabled_fields_do_not_change_scores_or_snapshots(market):
    a, b = [player(market, i) for i in range(2)]
    MatchingProfile.objects.filter(hackathon=market).update(matching_fields=['goal'])
    before = matching.recommendations(market, a)['results']
    MatchingProfile.objects.filter(participant=b).update(
        interests=['SECRET'], skills={'ai': 1}, slots=[900], collaboration_style=['reflect']
    )
    b.keywords = ['PRIVATE']
    b.save()
    after = matching.recommendations(market, a)['results']
    assert before[0]['compatibility'] == after[0]['compatibility']
    assert after[0]['compatibility_label'] == '资料不足，待交流确认'
    runs = str(list(RecommendationRun.objects.values('snapshot', 'results')))
    assert 'SECRET' not in runs and 'PRIVATE' not in runs and 'reflect' not in runs
    MatchingProfile.objects.filter(participant=b).update(matching_fields=[])
    assert matching.recommendations(market, a)['results'] == []


@pytest.mark.django_db
def test_new_fields_require_consent_and_public_visibility_is_separate(market):
    a, b = [player(market, i) for i in range(2)]
    a.keywords = b.keywords = ['校园']
    a.save()
    b.save()
    MatchingProfile.objects.filter(hackathon=market).update(collaboration_style=['plan'])
    profiles, _ = matching.core_profiles(market)
    assert profiles[str(a.pk)].keywords is None
    assert profiles[str(b.pk)].collaboration_style is None
    MatchingProfile.objects.filter(hackathon=market).update(
        matching_fields=['keywords', 'collaboration_style'], public_fields=[]
    )
    result = matching.recommendations(market, a)['results'][0]
    assert result['compatibility'] == 100
    assert result['coverage_percent'] == 20
    assert {r['code'] for r in result['reasons']} == {'KEYWORDS', 'STYLE'}
    person = matching.public_person(b, MatchingProfile.objects.get(participant=b))
    assert person['skills'] == {} and person['interests'] == []


@pytest.mark.django_db
def test_team_recommendation_uses_all_members_and_open_capacity(market):
    a, b, c = [player(market, i) for i in range(3)]
    track = Track.objects.create(hackathon=market, name='方向', state='published')
    team = Team.objects.create(
        hackathon=market, name='队伍', track=track, captain=b, recruiting=True, state='recruiting'
    )
    for p in [b, c]:
        p.team = team
        p.save()
    assert matching.recommendations(market, a, 'teams')['results']
    MatchingProfile.objects.filter(participant=c).update(opt_in=False)
    assert matching.recommendations(market, a, 'teams')['results'] == []
    MatchingProfile.objects.filter(participant=c).update(opt_in=True)
    team.recruiting = False
    team.save()
    assert matching.recommendations(market, a, 'teams')['results'] == []
    team.recruiting = True
    team.save()
    market.team_size = 2
    market.save()
    assert matching.recommendations(market, a, 'teams')['results'] == []


@pytest.mark.django_db
def test_form_revocation_persists_and_invalid_fields_rejected(market):
    a = player(market, 1)
    profile = MatchingProfile.objects.get(participant=a)
    data = profile_initial(profile, None)
    data.update(matching_fields=[], public_fields=[], collaboration_style=['plan'])
    form = ProfileForm(data)
    assert form.is_valid(), form.errors
    matching.save_profile(market, a.user, form.cleaned_data)
    profile.refresh_from_db()
    assert profile.matching_fields == [] and profile.public_fields == []
    assert matching.recommendations(market, a)['results'] == []
    data['matching_fields'] = ['contact']
    assert not ProfileForm(data).is_valid()


@pytest.mark.django_db
def test_authenticated_pages_expose_controls_and_explanations(market, client):
    a, b = [player(market, i) for i in range(2)]
    client.force_login(a.user)
    response = client.get(reverse('hackathon:matching-profile', args=[market.slug]))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'name="matching_fields"' in html
    assert 'name="public_fields"' in html
    assert 'name="collaboration_style"' in html
    response = client.get(reverse('hackathon:matching', args=[market.slug]))
    assert response.status_code == 200
    assert '可用资料覆盖' in response.content.decode()


@pytest.mark.django_db
def test_recommendations_only_preserve_existing_team_flow(market, client):
    market.matching_roster_enabled = False
    market.save()
    a = player(market, 1)
    client.force_login(a.user)
    response = client.get(reverse('hackathon:team-edit', args=[market.slug]))
    assert response.status_code == 200
    response = client.get(reverse('hackathon:teams', args=[market.slug]))
    assert response.status_code == 200
    assert '匹配推荐与组队进度' in response.content.decode()
    assert '完整名单需每位成员同意' not in response.content.decode()
    track = Track.objects.create(hackathon=market, name='方向', state='published')
    form = TeamForm(
        {'name': '发现小队', 'track': track.pk, 'intro': '一起探索', 'needs': '开发', 'recruiting': True, 'version': 1},
        hackathon=market,
    )
    assert form.is_valid(), form.errors
    team = services.save_team(market, a, form)
    b = player(market, 2)
    services.join_team(market, b, team, invite_code=team.invite_code)
    b.refresh_from_db()
    assert b.team_id == team.pk
    response = client.get(reverse('hackathon:matching', args=[market.slug]))
    assert 'name="operation" value="leave"' not in response.content.decode()
    assert 'name="operation" value="transfer"' not in response.content.decode()
