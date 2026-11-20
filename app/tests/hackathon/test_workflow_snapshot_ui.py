from copy import deepcopy
from uuid import UUID

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils import timezone

from eventyay.hackathon.workflow_models import FinalistContact, JudgingRound, ResultSnapshot


pytestmark = pytest.mark.django_db


def endpoint(hackathon, name, snapshot):
    return reverse(
        f'hackathon:workflow-{name}',
        kwargs={'hackathon_slug': hackathon.slug, 'snapshot_id': snapshot.pk},
    )


def entry(position, status='finalist', award=''):
    return {
        'position': position,
        'project_id': str(UUID(int=position)),
        'track': '跨学科创新赛道',
        'team': f'第{position}队',
        'rank': position,
        'average': 8.5,
        'status': status,
        'award': award,
    }


def make_snapshot(hackathon, signer, entries, *, kind='finalists', signed=True):
    round = JudgingRound.objects.create(
        hackathon=hackathon,
        kind='final' if kind == 'awards' else 'preliminary',
        version=1,
        name='冻结来源轮次',
        rules={},
        panel=[],
        tasks=[],
        signer=signer,
        signer_nonstudent=True,
        frozen_at=timezone.now(),
        frozen_by=signer,
    )
    return ResultSnapshot.objects.create(
        hackathon=hackathon,
        round=round,
        kind=kind,
        version=1,
        manifest={'material_snapshot_id': str(UUID(int=100)), 'entries': entries},
        sha256='d' * 64,
        reason='按既定规则签认的合成测试名单',
        signed_by=signer if signed else None,
        signed_at=timezone.now() if signed else None,
    )


def snapshot_page(client, hackathon, snapshot):
    response = client.get(endpoint(hackathon, 'snapshot', snapshot))
    assert response.status_code == 200
    return BeautifulSoup(response.content, 'html.parser').select_one('.workflow-snapshot')


def test_display_order_and_qualification_do_not_change_signed_manifest(client, hackathon, organizer_user):
    snapshot = make_snapshot(
        hackathon,
        organizer_user,
        [entry(3, 'excluded'), entry(1), entry(2, 'alternate'), entry(4, 'withdrawn'), entry(5, 'unexpected')],
    )
    original_manifest = deepcopy(snapshot.manifest)
    original_hash = snapshot.sha256
    original_signed_at = snapshot.signed_at
    client.force_login(organizer_user)

    page = snapshot_page(client, hackathon, snapshot)
    rows = page.select('[data-snapshot-entry]')
    assert [int(row['data-position']) for row in rows] == [1, 2, 3, 4, 5]
    assert [row.select_one('[data-snapshot-result]').get_text(strip=True) for row in rows] == [
        '入围',
        '候补',
        '未入围',
        '已退出',
        '待核对',
    ]
    for row in rows:
        assert [label.get_text(strip=True) for label in row.select('dt')] == [
            '顺序',
            '赛道 / 队伍',
            '名次 / 平均分',
            '资格',
        ]

    snapshot.refresh_from_db()
    assert snapshot.manifest == original_manifest
    assert snapshot.sha256 == original_hash
    assert snapshot.signed_at == original_signed_at
    exported = client.get(endpoint(hackathon, 'export', snapshot)).json()
    assert exported['manifest'] == original_manifest
    assert [row['position'] for row in exported['manifest']['entries']] == [3, 1, 2, 4, 5]
    assert exported['sha256'] == original_hash


def test_awards_use_named_award_instead_of_raw_stage_status(client, hackathon, organizer_user):
    entries = [entry(index, 'award', '最佳创意奖' if index == 7 else '') for index in range(1, 25)]
    snapshot = make_snapshot(hackathon, organizer_user, entries, kind='awards')
    client.force_login(organizer_user)

    page = snapshot_page(client, hackathon, snapshot)
    results = [node.get_text(strip=True) for node in page.select('[data-snapshot-result]')]
    assert len(results) == 24
    assert results.count('最佳创意奖') == 1
    assert results.count('未获奖') == 23
    assert 'award' not in results
    assert not page.select('button[value="queue"], button[value="promote"]')
    snapshot.refresh_from_db()
    assert snapshot.manifest['entries'] == entries


def test_signed_finalist_page_retains_source_and_authorized_actions(client, hackathon, organizer_user, team):
    row = entry(1)
    row['team'] = '用于验证小屏换行的超长队伍名称' * 4
    snapshot = make_snapshot(hackathon, organizer_user, [row])
    contact = FinalistContact.objects.create(hackathon=hackathon, snapshot=snapshot, team=team)
    client.force_login(organizer_user)

    page = snapshot_page(client, hackathon, snapshot)
    source = page.select_one('.snapshot-source details')
    assert source.summary.get_text(strip=True) == '查看来源与校验值'
    assert snapshot.sha256 in source.get_text()
    assert snapshot.manifest['material_snapshot_id'] in source.get_text()
    assert row['project_id'] in page.select_one('.snapshot-project-id').get_text()
    assert row['team'] in page.get_text()
    assert page.select_one(f'a[href="{endpoint(hackathon, "export", snapshot)}"]')
    confirmation_url = endpoint(hackathon, 'confirm', snapshot)
    assert page.select_one(f'a[href="{confirmation_url}"]').get_text(strip=True) == '打开本版队长确认入口'
    contact_url = reverse(
        'hackathon:workflow-contact', kwargs={'hackathon_slug': hackathon.slug, 'contact_id': contact.pk}
    )
    assert page.select_one(f'a[href="{contact_url}"]')
    for action in ['queue', 'promote']:
        button = page.select_one(f'button[name="action"][value="{action}"]')
        assert button
        form = button.find_parent('form')
        assert form['method'] == 'post'
        assert form.select_one('input[name="csrfmiddlewaretoken"]')
    assert page.select_one('input[name="outgoing"][required]')
    assert page.select_one('input[name="incoming"][required]')
    assert page.select_one('textarea[name="reason"][required][maxlength="2000"]')
    assert not page.select_one('button[value="sign"]')


def test_unsigned_snapshot_retains_signing_hash_and_requires_authorization(client, hackathon, organizer_user, user):
    snapshot = make_snapshot(hackathon, organizer_user, [entry(1)], signed=False)
    client.force_login(organizer_user)
    page = snapshot_page(client, hackathon, snapshot)
    form = page.select_one('button[value="sign"]').find_parent('form')
    assert form['method'] == 'post'
    assert form.select_one('input[name="sha256"]')['value'] == snapshot.sha256
    assert form.select_one('input[name="csrfmiddlewaretoken"]')
    assert not page.select('button[value="queue"], button[value="promote"]')

    client.force_login(user)
    assert client.get(endpoint(hackathon, 'snapshot', snapshot)).status_code == 403
    assert client.get(endpoint(hackathon, 'export', snapshot)).status_code == 403
