import pytest
from bs4 import BeautifulSoup

from eventyay.base.models import User
from eventyay.hackathon.models import CommunityPost, Participant
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def test_empty_directory_examples_are_guidance_without_creating_records(client, hackathon):
    response = client.get(endpoint(hackathon, 'teams'))
    assert response.status_code == 200
    assert not response.context['seekers']
    assert not response.context['community_posts']
    assert not response.context['pool_posts']
    assert not Participant.objects.exists()
    assert not CommunityPost.objects.exists()
    soup = BeautifulSoup(response.content, 'html.parser')
    for section_id in ['people', 'community', 'work-pool']:
        section = soup.select_one(f'#{section_id}')
        assert not section.select('.showcase-results article')
        assert not section.select_one('details').has_attr('open')
        examples = section.select('.showcase-example')
        assert len(examples) == 3
        assert all('写法示例' in card.text for card in examples)
        assert not section.select('.showcase-example a')
        assert section.select_one('a.showcase-publish') is not None


@pytest.mark.parametrize('kind', ['experience', 'question', 'help', 'idea', 'showcase', 'co_create'])
def test_post_kind_prefill_keeps_real_draft_empty(client, hackathon, participant, kind):
    # Example cards are deliberately read-only, collapsed guidance. Kind-prefilled
    # writing links remain a supported direct entry, without copying sample text.
    client.force_login(participant.user)
    response = client.get(endpoint(hackathon, 'post-new'), {'kind': kind})
    assert response.status_code == 200
    form = response.context['form']
    assert form['kind'].value() == kind
    assert not form['title'].value()
    assert not form['body'].value()
    assert not CommunityPost.objects.exists()


@pytest.mark.parametrize('kind', ['unknown', '<script>alert(1)</script>'])
def test_invalid_kind_preset_is_ignored(client, hackathon, participant, kind):
    client.force_login(participant.user)
    default = client.get(endpoint(hackathon, 'post-new')).context['form']['kind'].value()
    response = client.get(endpoint(hackathon, 'post-new'), {'kind': kind})
    assert response.status_code == 200
    assert response.context['form']['kind'].value() == default
    assert not CommunityPost.objects.exists()


def test_posted_kind_wins_over_example_and_validation_keeps_user_input(client, hackathon, participant):
    client.force_login(participant.user)
    location = endpoint(hackathon, 'post-new') + '?kind=showcase'
    data = {'kind': 'help', 'title': '真实求助', 'body': '', 'invitation': '一起排查问题'}
    response = client.post(location, data)
    assert response.status_code == 200
    form = response.context['form']
    assert 'body' in form.errors
    assert form['kind'].value() == 'help'
    assert form['title'].value() == '真实求助'
    assert not CommunityPost.objects.exists()
    data['body'] = '已经尝试过重现和检查请求。'
    response = client.post(location, data)
    assert response.status_code == 302
    post = CommunityPost.objects.get()
    assert post.kind == 'help'
    assert post.author == participant
    assert not post.is_public
    response = client.get(endpoint(hackathon, 'post-edit', post_id=post.pk), {'kind': 'idea'})
    assert response.context['form']['kind'].value() == 'help'


@pytest.mark.parametrize('matching_enabled', [False, True])
def test_intro_action_edits_actual_intro_in_both_modes(client, hackathon, participant, matching_enabled):
    hackathon.matching_enabled = matching_enabled
    hackathon.save()
    client.force_login(participant.user)
    response = client.get(endpoint(hackathon, 'teams'))
    soup = BeautifulSoup(response.content, 'html.parser')
    link = soup.select_one('#people .showcase-publish')['href']
    assert link == endpoint(hackathon, 'register')
    profile = client.get(link)
    assert {'tagline', 'bio', 'skills', 'keywords'} <= profile.context['form'].fields.keys()
    if matching_enabled:
        assert soup.select_one('#people .showcase-publish-note a')['href'] == endpoint(hackathon, 'matching-profile')


def test_real_entries_are_separate_from_examples_and_private_fields_stay_hidden(client, hackathon, participant):
    participant.looking_for_team = True
    participant.tagline = '真实公开介绍'
    participant.save()
    hidden = Participant.objects.create(
        hackathon=hackathon,
        user=User.objects.create_user(email='hidden-showcase@example.org'),
        name='PRIVATE_SEEKER',
        consent=True,
    )
    for public, title in [(True, '真实作品进展'), (False, 'PRIVATE_DRAFT')]:
        CommunityPost.objects.create(
            hackathon=hackathon,
            author=participant,
            kind='showcase',
            title=title,
            body='项目已有原型',
            is_public=public,
        )
    response = client.get(endpoint(hackathon, 'teams'))
    soup = BeautifulSoup(response.content, 'html.parser')
    assert list(response.context['seekers']) == [participant]
    assert '真实公开介绍' in soup.select_one('#people .showcase-results').text
    assert '真实作品进展' in soup.select_one('#work-pool .showcase-results').text
    assert not soup.select('.showcase-results .showcase-example')
    text = soup.text
    for private in [hidden.name, participant.contact, participant.availability, 'PRIVATE_DRAFT']:
        assert private not in text
