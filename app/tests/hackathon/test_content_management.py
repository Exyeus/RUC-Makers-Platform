import pytest
from django.urls import reverse

from eventyay.hackathon.views import CONTENT


@pytest.mark.django_db
@pytest.mark.parametrize(
    'kind,data',
    [
        ('announcement', {'title': '参赛通知', 'body': '请准时到场。', 'audience': 'all'}),
        (
            'resource',
            {
                'title': '开发指南',
                'category': 'guide',
                'summary': '开始开发',
                'body': '准备开发环境。',
                'minutes': 5,
                'position': 0,
            },
        ),
        (
            'schedule',
            {
                'title': '开幕环节',
                'kind': '活动',
                'starts_at': '2026-10-01T09:00',
                'ends_at': '2026-10-01T10:00',
                'location': '报告厅',
            },
        ),
        ('track', {'name': '开放创新赛道', 'description': '发挥创意。'}),
    ],
)
def test_content_create_list_and_edit_stay_in_hub(client, hackathon, organizer_user, kind, data):
    client.force_login(organizer_user)
    kwargs = {'hackathon_slug': hackathon.slug, 'kind': kind}
    location = reverse('hackathon:content-new', kwargs=kwargs)
    response = client.post(location, data, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(reverse('hackathon:content', kwargs={'hackathon_slug': hackathon.slug}), 302)]
    assert 'hackathon/content.html' in [template.name for template in response.templates]
    assert 'hackathon/base.html' in [template.name for template in response.templates]
    label_field = 'name' if kind == 'track' else 'title'
    assert data[label_field] in response.content.decode()

    model = CONTENT[kind][0]
    item = model.objects.get(hackathon=hackathon)
    edit_url = reverse('hackathon:content-edit', kwargs={**kwargs, 'item_id': item.pk})
    assert edit_url in response.content.decode()
    response = client.get(edit_url)
    assert response.status_code == 200
    assert 'hackathon/form.html' in [template.name for template in response.templates]

    updated = {**data, label_field: data[label_field] + '（已更新）'}
    response = client.post(edit_url, updated, follow=True)
    assert response.status_code == 200
    assert updated[label_field] in response.content.decode()
    item.refresh_from_db()
    assert getattr(item, label_field) == updated[label_field]
