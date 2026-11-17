"""Ownership, discovery and idea-sharing remain distinct without relaxing privacy."""

import pytest
from bs4 import BeautifulSoup

from eventyay.base.models import User
from eventyay.hackathon.models import (
    CommunityPost,
    Idea,
    JoinRequest,
    MatchingBlock,
    MatchingProfile,
    Participant,
    SocialPreference,
    Team,
    Track,
)
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def person(hackathon, name, **kwargs):
    user = User.objects.create_user(email=f'{name}@example.org', fullname=name)
    return Participant.objects.create(hackathon=hackathon, user=user, name=name, consent=True, **kwargs)


def post(hackathon, author, title, **kwargs):
    return CommunityPost.objects.create(
        hackathon=hackathon, author=author, title=title, body='交流正文', invitation='一起讨论', **kwargs
    )


def idea(hackathon, author, title, **kwargs):
    return Idea.objects.create(
        hackathon=hackathon,
        author=author,
        title=title,
        problem='校园问题',
        audience='同学',
        solution='验证方案',
        mode='either',
        **kwargs,
    )


def test_sections_separate_ownership_discovery_and_private_drafts(client, hackathon, team, participant):
    other = person(hackathon, 'other-captain')
    other_team = Team.objects.create(hackathon=hackathon, captain=other, track=team.track, name='OTHER_RECRUITING_TEAM')
    post(hackathon, participant, 'OWN_PRIVATE_DRAFT', is_public=False)
    post(hackathon, other, 'PUBLIC_DISCUSSION', is_public=True)
    client.force_login(participant.user)
    own = client.get(endpoint(hackathon, 'teams')).content.decode()
    directory = client.get(endpoint(hackathon, 'team-directory')).content.decode()
    exchange = client.get(endpoint(hackathon, 'idea-exchange')).content.decode()
    assert team.invite_code in own
    assert team.invite_code not in directory + exchange
    assert other_team.invite_code not in own + directory + exchange
    assert 'OTHER_RECRUITING_TEAM' not in own
    assert 'OTHER_RECRUITING_TEAM' in directory
    assert 'PUBLIC_DISCUSSION' not in own + directory
    assert 'PUBLIC_DISCUSSION' in exchange
    assert 'OWN_PRIVATE_DRAFT' not in own + directory
    soup = BeautifulSoup(exchange, 'html.parser')
    assert 'OWN_PRIVATE_DRAFT' in soup.select_one('#my-posts').text
    assert 'OWN_PRIVATE_DRAFT' not in soup.select_one('#community').text
    assert not BeautifulSoup(own, 'html.parser').select('#team-results, #people, #community, #work-pool')
    assert not BeautifulSoup(directory, 'html.parser').select('#community, #work-pool, #my-posts')
    assert not soup.select('#team-results, #people, .own-team')


def test_only_my_team_requires_login_and_never_leaks_private_content_to_guests(client, hackathon, team, participant):
    post(hackathon, participant, 'PRIVATE_GUEST_DRAFT', is_public=False)
    response = client.get(endpoint(hackathon, 'teams'))
    assert response.status_code == 302
    assert endpoint(hackathon, 'student-login') in response.url
    for route in ('team-directory', 'idea-exchange'):
        response = client.get(endpoint(hackathon, route))
        assert response.status_code == 200
        assert team.invite_code not in response.content.decode()
        assert 'PRIVATE_GUEST_DRAFT' not in response.content.decode()


def test_signed_in_user_without_participant_has_an_actionable_own_team_page(client, hackathon, user):
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'teams'))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    assert soup.select_one('main a[href="' + endpoint(hackathon, 'register') + '"]')
    assert not soup.select('.own-team, #team-results, #people, #community')


@pytest.mark.parametrize('route', ['teams', 'team-directory', 'idea-exchange'])
def test_cross_section_navigation_marks_the_actual_destination(client, hackathon, participant, route):
    client.force_login(participant.user)
    response = client.get(endpoint(hackathon, route))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    navigation = soup.select_one('nav[aria-label="队伍与交流栏目"]')
    assert navigation
    assert {a['href'] for a in navigation.select('a')} == {
        endpoint(hackathon, name) for name in ('teams', 'team-directory', 'idea-exchange')
    }
    assert [a['href'] for a in navigation.select('[aria-current="page"]')] == [endpoint(hackathon, route)]
    assert response.context['navigation_active'] == route


def test_discovery_filters_only_discovery_and_exchange_filters_only_ideas(client, hackathon, team, participant):
    seeker = person(hackathon, 'match-seeker', looking_for_team=True, keywords=['Python'])
    seeker.tagline = '共同方向'
    seeker.save()
    team.name = '共同方向'
    team.save()
    public = post(hackathon, participant, '共同方向问题', kind='question', is_public=True)
    post(hackathon, participant, '另一方向展示', kind='showcase', is_public=True)
    response = client.get(
        endpoint(hackathon, 'team-directory'),
        {
            'q': '共同方向',
            'track': str(team.track_id),
            'keyword': 'Python',
            'kind': 'showcase',
        },
    )
    assert list(response.context['teams']) == [team]
    assert list(response.context['seekers']) == [seeker]
    soup = BeautifulSoup(response.content, 'html.parser')
    form = soup.select_one('form.team-filters')
    assert form['method'] == 'get' and form['action'] == endpoint(hackathon, 'team-directory')
    assert not form.select('[name="kind"]')
    exchange = client.get(endpoint(hackathon, 'idea-exchange'), {'q': '共同方向', 'kind': 'question', 'track': '999'})
    assert list(exchange.context['community_posts']) == [public]
    form = BeautifulSoup(exchange.content, 'html.parser').select_one('form.idea-filters')
    assert form['method'] == 'get' and form['action'] == endpoint(hackathon, 'idea-exchange')
    assert not form.select('[name="track"], [name="keyword"]')


def test_exchange_pool_honors_search_and_content_kind(client, hackathon, participant):
    hackathon.matching_enabled = True
    hackathon.save()
    client.force_login(participant.user)
    wanted = post(hackathon, participant, '共同展示', kind='showcase', is_public=True)
    post(hackathon, participant, '不相关展示', kind='showcase', is_public=True)
    question = post(hackathon, participant, '共同问题', kind='question', is_public=True)
    native = idea(hackathon, participant, '共同原生想法')
    result = client.get(endpoint(hackathon, 'idea-exchange'), {'q': '共同', 'kind': 'showcase'})
    assert list(result.context['community_posts']) == [wanted]
    assert list(result.context['pool_posts']) == [wanted]
    assert not list(result.context['pool_ideas'])
    result = client.get(endpoint(hackathon, 'idea-exchange'), {'q': '共同', 'kind': 'question'})
    assert list(result.context['community_posts']) == [question]
    assert not list(result.context['pool_posts']) and not list(result.context['pool_ideas'])
    result = client.get(endpoint(hackathon, 'idea-exchange'), {'q': '共同', 'kind': 'idea'})
    assert list(result.context['pool_ideas']) == [native]
    assert not list(result.context['pool_posts'])


def test_split_preserves_hidden_fields_blocks_and_private_drafts(client, hackathon, participant):
    hackathon.matching_enabled = True
    hackathon.save()
    hidden = person(
        hackathon,
        'visible-name',
        looking_for_team=True,
        bio='HIDDEN_BIO',
        keywords=['Python'],
        contact='PRIVATE_CONTACT',
    )
    blocked = person(hackathon, 'BLOCKED_PERSON', looking_for_team=True)
    for p in (participant, hidden, blocked):
        MatchingProfile.objects.create(hackathon=hackathon, participant=p, opt_in=True, visibility='event')
    SocialPreference.objects.create(hackathon=hackathon, participant=hidden, hidden_fields=['bio', 'keywords'])
    MatchingBlock.objects.create(hackathon=hackathon, source=participant, target=blocked)
    track = Track.objects.create(hackathon=hackathon, name='公开赛道', state='published')
    blocked_team = Team.objects.create(hackathon=hackathon, captain=blocked, track=track, name='BLOCKED_TEAM')
    blocked.team = blocked_team
    blocked.save()
    post(hackathon, blocked, 'BLOCKED_POST', is_public=True)
    post(hackathon, hidden, 'OTHER_PRIVATE_DRAFT', is_public=False)
    idea(hackathon, blocked, 'BLOCKED_IDEA')
    client.force_login(participant.user)
    directory = client.get(endpoint(hackathon, 'team-directory')).content.decode()
    assert hidden.name in directory
    for value in ('HIDDEN_BIO', 'PRIVATE_CONTACT', 'BLOCKED_PERSON', 'BLOCKED_TEAM'):
        assert value not in directory
    for values in ({'q': 'HIDDEN_BIO'}, {'keyword': 'Python'}):
        assert hidden not in client.get(endpoint(hackathon, 'team-directory'), values).context['seekers']
    exchange = client.get(endpoint(hackathon, 'idea-exchange')).content.decode()
    for value in ('BLOCKED_POST', 'BLOCKED_IDEA', 'OTHER_PRIVATE_DRAFT'):
        assert value not in exchange
    hidden_draft = CommunityPost.objects.get(title='OTHER_PRIVATE_DRAFT')
    assert client.get(endpoint(hackathon, 'post-detail', post_id=hidden_draft.pk)).status_code == 404


def test_application_history_stays_in_discovery_and_current_progress_is_on_my_team(client, hackathon, team):
    applicant = person(hackathon, 'applicant')
    client.force_login(applicant.user)
    response = client.post(
        endpoint(hackathon, 'team-action'), {'action': 'apply', 'team': team.pk, 'message': '想参加'}
    )
    assert response.status_code == 302
    assert response.url.split('#')[0] == endpoint(hackathon, 'team-directory')
    application = JoinRequest.objects.get(participant=applicant, team=team)
    directory = BeautifulSoup(client.get(endpoint(hackathon, 'team-directory')).content, 'html.parser')
    mine = directory.select_one('#my-applications')
    assert mine and team.name in mine.text
    own = BeautifulSoup(client.get(endpoint(hackathon, 'teams')).content, 'html.parser')
    assert not own.select('#my-applications')
    assert team.name in own.select_one('#team-progress').text
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'withdraw', 'application': application.pk})
    assert response.url.split('#')[0] == endpoint(hackathon, 'team-directory')
    application.refresh_from_db()
    assert application.state == 'withdrawn'


def test_captain_actions_return_to_management_and_post_forms_return_to_idea_context(
    client, hackathon, team, participant
):
    client.force_login(participant.user)
    old_code = team.invite_code
    response = client.post(endpoint(hackathon, 'team-action'), {'action': 'rotate'})
    assert response.status_code == 302 and response.url == endpoint(hackathon, 'teams')
    team.refresh_from_db()
    assert team.invite_code != old_code
    response = client.post(
        endpoint(hackathon, 'post-new'),
        {
            'kind': 'idea',
            'title': '新的想法草稿',
            'body': '从真实问题开始',
            'invitation': '欢迎一起讨论',
        },
    )
    draft = CommunityPost.objects.get(title='新的想法草稿')
    assert response.status_code == 302 and response.url == endpoint(hackathon, 'post-detail', post_id=draft.pk)
    detail = client.get(response.url)
    assert detail.context['navigation_active'] == 'idea-exchange'
    assert endpoint(hackathon, 'idea-exchange') in detail.content.decode()
    editing = client.get(endpoint(hackathon, 'team-edit'))
    assert editing.context['navigation_active'] == 'teams'


def test_native_idea_form_keeps_exchange_navigation_and_returns_after_save(client, hackathon, participant):
    hackathon.matching_enabled = True
    hackathon.matching_idea_min_chars = 2
    hackathon.save()
    client.force_login(participant.user)
    location = endpoint(hackathon, 'idea-create')
    page = client.get(location)
    assert page.status_code == 200
    assert page.context['navigation_active'] == 'idea-exchange'
    soup = BeautifulSoup(page.content, 'html.parser')
    assert soup.select_one('main .actions a')['href'] == endpoint(hackathon, 'idea-exchange')
    response = client.post(
        location,
        {
            'idempotency_key': page.context['request_key'],
            'version': 0,
            'title': '新的原生想法',
            'problem': '真实的问题',
            'audience': '服务的同学',
            'solution': '验证的原型',
            'mode': 'either',
            'state': 'open',
            'interest_tags': '校园',
            'roles': ['frontend'],
        },
    )
    assert response.status_code == 302
    assert response.url == endpoint(hackathon, 'idea-exchange')
    assert Idea.objects.filter(hackathon=hackathon, author=participant, title='新的原生想法').exists()
