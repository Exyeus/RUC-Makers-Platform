import pytest
from bs4 import BeautifulSoup

from eventyay.base.models import User
from eventyay.hackathon.models import CommunityPost, Participant, Team, Track
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


def test_combined_filters_preserve_selections_and_target_results(client, hackathon, team, participant):
    team.name = '协作队伍'
    team.save()
    other_track = Track.objects.create(hackathon=hackathon, name='另一赛道', state='published')
    Team.objects.create(hackathon=hackathon, captain=participant, track=other_track, name='协作另一队')
    people = []
    for index, (keyword, public) in enumerate([('Python', True), ('Rust', True), ('Python', False)]):
        person = Participant.objects.create(
            hackathon=hackathon,
            user=User.objects.create_user(email=f'filter-{index}@example.org'),
            name=f'协作伙伴{index}',
            keywords=[keyword],
            looking_for_team=public,
            consent=True,
        )
        people.append(person)
    posts = [
        CommunityPost.objects.create(
            hackathon=hackathon,
            author=participant,
            kind=kind,
            title=f'协作{kind}',
            body='寻找新的合作方式',
            invitation='一起交流',
        )
        for kind in ['question', 'showcase']
    ]
    values = {'q': '协作', 'keyword': 'Python', 'track': str(team.track_id), 'kind': 'question'}
    response = client.get(endpoint(hackathon, 'teams'), values)
    assert response.status_code == 200
    assert list(response.context['teams']) == [team]
    assert list(response.context['seekers']) == [people[0]]
    assert list(response.context['community_posts']) == [posts[0]]
    soup = BeautifulSoup(response.content, 'html.parser')
    form = soup.select_one('form.team-filters')
    # One GET submission carries every selection instead of losing the other form's state.
    submitted = {control['name']: control['value'] for control in form.select('input:checked, input[name="q"]')}
    assert submitted == values
    assert form['method'] == 'get'
    assert not form.select('select')
    assert form.select_one('input[value="Python"]').find_parent('label').text.strip() == 'Python'


@pytest.mark.parametrize('keyword', ['空间数据 / GIS', '<img src=x onerror=alert(1)>'])
def test_custom_keyword_remains_selected_and_escaped(client, hackathon, keyword):
    response = client.get(endpoint(hackathon, 'teams'), {'keyword': keyword})
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    checked = soup.select_one('.team-filters input[name="keyword"]:checked')
    assert checked['value'] == keyword
    assert keyword in checked.find_parent('label').text
    assert '自定义关键词' in soup.select_one('.team-filters').text
    assert not soup.select('.team-filters img, .team-filters script')
