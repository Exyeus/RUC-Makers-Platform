from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Event, User
from eventyay.hackathon.calendar import attendance_hours, schedule_heatmap
from eventyay.hackathon.forms import ParticipantForm
from eventyay.hackathon.matching_forms import ProfileForm, profile_initial, tags
from eventyay.hackathon.models import (
    BonusClaim,
    BonusRule,
    CommunityPost,
    CommunityResponse,
    Hackathon,
    Idea,
    MatchingProfile,
    Participant,
    ScheduleItem,
)
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db


@pytest.fixture
def other_participant(hackathon):
    user = User.objects.create_user(email='camp-partner@example.org', fullname='新伙伴')
    return Participant.objects.create(
        hackathon=hackathon, user=user, name='新伙伴', school='人大', contact='SECRET', consent=True
    )


@pytest.fixture
def community_post(hackathon, participant):
    return CommunityPost.objects.create(
        hackathon=hackathon,
        author=participant,
        kind='showcase',
        title='一个校园小作品',
        body='试着解决选课时间冲突',
        invitation='一起试试，给点建议',
    )


def test_camp_navigation_and_calendar(client, hackathon, participant, user):
    client.force_login(user)
    response = client.get(endpoint(hackathon, 'home'))
    assert response.status_code == 200
    navigation = [item[0] for item in response.context['navigation']]
    assert 'register' not in navigation
    assert 'announcements' not in navigation
    assert 'matching' not in navigation
    assert {'teams', 'bonus'} <= set(navigation)
    body = response.content.decode()
    assert 'aria-label="个人资料"' in body
    assert 'aria-label="通知公告"' in body
    assert '活动小时历' in body
    assert '查看活动日程' not in body


@pytest.mark.parametrize(
    'name', ['home', 'student-login', 'register', 'teams', 'matching', 'bonus', 'stage-state', 'manage']
)
def test_hackathon_unavailable_for_every_role(client, hackathon, organizer_user, name):
    hackathon.program = Hackathon.Program.HACKATHON
    hackathon.save()
    assert client.get(endpoint(hackathon, name)).status_code == 404
    client.force_login(organizer_user)
    assert client.get(endpoint(hackathon, name)).status_code == 404


def test_hackathon_not_listed_and_station_blocked(client, hackathon):
    hackathon.program = Hackathon.Program.HACKATHON
    hackathon.save()
    assert hackathon.name not in client.get('/hackathon/').content.decode()
    assert client.get(endpoint(hackathon, 'station-state'), HTTP_AUTHORIZATION='Bearer anything').status_code == 404


def test_hour_calendar_uses_event_timezone_and_all_overlapping_items(hackathon):
    due = hackathon.submission_deadline.replace(minute=30, second=0, microsecond=0)
    hackathon.submission_deadline = due
    ScheduleItem.objects.create(
        hackathon=hackathon,
        title='同小时答疑',
        starts_at=due - timedelta(minutes=45),
        ends_at=due + timedelta(hours=1),
        location='创客教室',
    )
    days = schedule_heatmap(hackathon)
    local = timezone.localtime(due, timezone.get_fixed_timezone(480))
    day = next(day for day in days if day['date'] == local.date())
    cell = day['hours'][local.hour]
    assert cell['level'] == 'deadline'
    assert '作品提交截止' in cell['label'] and '同小时答疑' in cell['label'] and '创客教室' in cell['label']
    assert day['hours'][(local.hour + 1) % 24]['level'] in {'event', 'empty'}
    assert all(len(day['hours']) == 24 for day in days)


def test_tags_have_no_count_cap_and_custom_tags_persist(client, hackathon, participant, user):
    client.force_login(user)
    selected = list(ParticipantForm.base_fields['keywords'].choices[0][1])
    selected = [value for value, _ in selected]
    response = client.post(
        endpoint(hackathon, 'register'),
        {
            'name': participant.name,
            'school': '人大',
            'contact': 'secret',
            'consent': 'on',
            'keywords': selected,
            'custom_keywords': '校园漫步，AI 创作, 校园漫步',
        },
    )
    assert response.status_code == 302
    participant.refresh_from_db()
    assert len(participant.keywords) > 6
    assert participant.keywords[-2:] == ['校园漫步', 'AI 创作']
    assert len(tags(','.join(f'标签{i}' for i in range(100)))) == 100
    form = ParticipantForm(instance=participant, hackathon=hackathon)
    assert '校园漫步' in form.initial['custom_keywords']
    assert len(form.fields['keywords'].choices) >= 8


def test_absence_grid_validates_event_hours_and_persists(client, hackathon, participant, user):
    hours = [hour for _, choices in attendance_hours(hackathon) for hour, _ in choices]
    selected = [hours[0], hours[-1]]
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'register'),
        {
            'name': participant.name,
            'school': '人大',
            'contact': 'secret',
            'consent': 'on',
            'unavailable_slots': selected,
        },
    )
    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.unavailable_slots == sorted(map(int, selected))
    invalid = client.post(
        endpoint(hackathon, 'register'),
        {
            'name': participant.name,
            'school': '人大',
            'contact': 'secret',
            'consent': 'on',
            'unavailable_slots': ['1'],
        },
    )
    assert invalid.status_code == 200 and 'unavailable_slots' in invalid.context['form'].errors
    participant.refresh_from_db()
    assert participant.unavailable_slots == sorted(map(int, selected))
    assert len(attendance_hours(hackathon)) == 2


def test_absence_hours_visible_to_organizer_and_export_only(client, hackathon, participant, organizer_user):
    hour = attendance_hours(hackathon)[0][1][0][0]
    participant.unavailable_slots = [int(hour)]
    participant.availability = ''
    participant.save()
    client.force_login(organizer_user)
    dashboard = client.get(endpoint(hackathon, 'manage'))
    assert list(dashboard.context['special'])[0].pk == participant.pk
    response = client.get(endpoint(hackathon, 'participants'))
    assert response.context['participants'][0].attendance_summary
    summary = response.context['participants'][0].attendance_summary
    assert summary in response.content.decode()
    assert summary in client.get(endpoint(hackathon, 'export', kind='participants')).content.decode()
    client.logout()
    assert summary not in client.get(endpoint(hackathon, 'teams')).content.decode()


def test_absence_grid_converts_to_matching_half_hours(hackathon):
    hours = [hour for _, choices in attendance_hours(hackathon) for hour, _ in choices]
    form = ProfileForm(
        {
            'version': 0,
            'visibility': 'event',
            'availability_confirmed': True,
            'unavailable_slots': [hours[0]],
            'suggested_interests': ['Python'],
            'interests': '校园',
        },
        hackathon=hackathon,
    )
    assert form.is_valid(), form.errors
    assert int(hours[0]) * 2 not in form.cleaned_data['slots']
    assert int(hours[1]) * 2 in form.cleaned_data['slots']
    assert form.cleaned_data['interests'] == ['Python', '校园']
    all_absent = ProfileForm(
        {
            'version': 0,
            'visibility': 'event',
            'availability_confirmed': True,
            'unavailable_slots': hours,
        },
        hackathon=hackathon,
    )
    assert all_absent.is_valid(), all_absent.errors
    assert all_absent.cleaned_data['slots'] == []


def test_confirmed_absences_survive_reload_and_registration_updates(client, hackathon, participant, user):
    profile = MatchingProfile.objects.create(hackathon=hackathon, participant=participant, slots=[])
    reloaded = ProfileForm(initial=profile_initial(profile, None), hackathon=hackathon)
    hours = [hour for _, choices in attendance_hours(hackathon) for hour, _ in choices]
    assert reloaded.initial['availability_confirmed']
    assert set(reloaded.initial['unavailable_slots']) == set(hours)
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'register'),
        {
            'name': participant.name,
            'school': '人大',
            'contact': 'private',
            'consent': True,
            'unavailable_slots': [hours[0]],
        },
    )
    assert response.status_code == 302
    profile.refresh_from_db()
    assert int(hours[0]) * 2 not in profile.slots
    assert int(hours[1]) * 2 in profile.slots
    assert profile.version == 2


def test_root_selects_camp_and_never_disabled_hackathon(client, hackathon):
    hackathon.program = Hackathon.Program.HACKATHON
    hackathon.save()
    with timezone.override('Asia/Shanghai'):
        call_command(
            'setup_hackathon',
            organizer=hackathon.event.organizer.slug,
            slug='root-camp',
            event='root-camp',
            publish=True,
            stdout=StringIO(),
        )
    response = client.get('/')
    assert response.status_code == 302
    assert response.url == '/hackathon/root-camp/'


def test_square_and_work_pool_show_camp_content_without_matching(client, hackathon, community_post):
    response = client.get(endpoint(hackathon, 'teams'))
    assert response.status_code == 200
    assert community_post.title in response.content.decode()
    assert '作品池' in response.content.decode()
    assert list(response.context['pool_posts']) == [community_post]
    hackathon.matching_enabled = True
    hackathon.save()
    assert client.get(endpoint(hackathon, 'teams')).status_code == 200


def test_square_preserves_matching_visibility(client, hackathon, participant, other_participant):
    hackathon.matching_enabled = True
    hackathon.save()
    participant.looking_for_team = True
    participant.save()
    profile = MatchingProfile.objects.create(
        hackathon=hackathon, participant=participant, opt_in=True, visibility='paused'
    )
    idea = Idea.objects.create(
        hackathon=hackathon,
        author=participant,
        title='REGISTERED_ONLY_IDEA',
        problem='校园问题',
        audience='同学',
        solution='一起研究',
        mode='either',
    )
    assert idea.title not in client.get(endpoint(hackathon, 'teams')).content.decode()
    client.force_login(other_participant.user)
    response = client.get(endpoint(hackathon, 'teams'))
    assert participant not in response.context['seekers']
    assert idea.title in response.content.decode()
    profile.visibility = 'event'
    profile.save()
    assert participant in client.get(endpoint(hackathon, 'teams')).context['seekers']


def test_post_create_edit_and_private_draft(client, hackathon, participant, user, other_participant):
    client.force_login(user)
    response = client.post(
        endpoint(hackathon, 'post-new'),
        {
            'kind': 'question',
            'title': '能帮忙想一想吗',
            'body': '校园生活的一个小问题',
            'invitation': '分享你的经历',
            'is_open': 'on',
        },
    )
    assert response.status_code == 302
    post = CommunityPost.objects.get(title='能帮忙想一想吗')
    assert not post.is_public
    assert client.get(response.url).status_code == 200
    client.force_login(other_participant.user)
    assert client.get(endpoint(hackathon, 'post-detail', post_id=post.pk)).status_code == 404
    assert client.get(endpoint(hackathon, 'post-edit', post_id=post.pk)).status_code == 404
    assert post.title not in client.get(endpoint(hackathon, 'teams')).content.decode()


def test_private_responses_only_visible_to_parties(client, hackathon, community_post, participant, other_participant):
    client.force_login(other_participant.user)
    location = endpoint(hackathon, 'post-detail', post_id=community_post.pk)
    response = client.post(location, {'body': 'PRIVATE_RESPONSE', 'private': 'on'})
    assert response.status_code == 302
    assert 'PRIVATE_RESPONSE' in client.get(location).content.decode()
    outsider = User.objects.create_user(email='outsider@example.org')
    client.force_login(outsider)
    assert 'PRIVATE_RESPONSE' not in client.get(location).content.decode()
    client.force_login(participant.user)
    assert 'PRIVATE_RESPONSE' in client.get(location).content.decode()
    community_post.is_open = False
    community_post.save()
    client.post(location, {'body': 'closed response'})
    assert CommunityResponse.objects.filter(post=community_post).count() == 1


def test_bonus_claim_review_idempotency_and_points_snapshot(client, hackathon, participant, user, organizer_user):
    rule = BonusRule.objects.create(
        hackathon=hackathon, title='完成工作坊', description='提交作品链接', points=5, active=True
    )
    location = endpoint(hackathon, 'bonus')
    client.force_login(user)
    assert client.post(location, {'rule': rule.pk, 'evidence': '完成说明'}).status_code == 302
    assert client.post(location, {'rule': rule.pk, 'evidence': '重复提交'}).status_code == 302
    claim = BonusClaim.objects.get(participant=participant)
    assert claim.awarded_points == 0
    review = {'operation': 'review', 'claim_id': claim.pk, 'decision': 'approved', 'note': '已核验作品'}
    assert client.post(location, review).status_code == 403
    client.force_login(organizer_user)
    assert client.post(location, review).status_code == 302
    rule.points = 10
    rule.save()
    client.post(location, review)
    claim.refresh_from_db()
    assert claim.awarded_points == 5 and claim.reviewer == organizer_user
    client.force_login(user)
    assert client.get(location).context['bonus_total'] == 5


def test_cross_event_post_and_bonus_are_inaccessible(client, hackathon, participant, user, community_post):
    other_event = Event.objects.create(
        organizer=hackathon.event.organizer,
        name='其他创客营',
        slug='other-camp',
        date_from=timezone.now(),
        date_to=timezone.now() + timedelta(days=2),
        currency='CNY',
    )
    other = Hackathon.objects.create(
        event=other_event,
        slug='other-camp',
        name='其他营',
        is_public=True,
        registration_deadline=hackathon.registration_deadline,
        team_freeze_at=hackathon.team_freeze_at,
        soft_deadline=hackathon.soft_deadline,
        submission_deadline=hackathon.submission_deadline,
        freeze_at=hackathon.freeze_at,
    )
    with scope(event=other_event):
        rule = BonusRule.objects.create(
            hackathon=other, title='其他营规则', description='其他营', points=10, active=True
        )
    client.force_login(user)
    assert client.get(endpoint(other, 'post-detail', post_id=community_post.pk)).status_code == 404
    response = client.post(endpoint(hackathon, 'bonus'), {'rule': rule.pk, 'evidence': '越界申请'})
    assert response.status_code == 200 and response.context['form'].errors
    assert not BonusClaim.objects.exists()


def test_setup_defaults_to_two_day_camp(hackathon):
    output = StringIO()
    call_command(
        'setup_hackathon', organizer=hackathon.event.organizer.slug, slug='camp-new', event='camp-new', stdout=output
    )
    created = Hackathon.objects.get(slug='camp-new')
    assert created.program == 'maker_camp'
    assert '黑客松' not in created.name
    assert (created.event.date_to.date() - created.event.date_from.date()).days == 1
