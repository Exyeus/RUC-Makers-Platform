from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.models import (
    DemoSlot,
    Participant,
    Project,
    Score,
    StaffAssignment,
    SubmissionSnapshot,
    Team,
    Track,
)
from eventyay.hackathon.workflow_forms import ROUND_CRITERIA, RoundScoreForm
from eventyay.hackathon.workflow_models import FinalistContact, RoundScore


pytestmark = pytest.mark.django_db


def make_judges(h, count=2):
    result = []
    for index in range(count):
        user = User.objects.create_user(email=f'judge-{User.objects.count()}-{index}@example.invalid')
        StaffAssignment.objects.create(hackathon=h, user=user, role='judge')
        result.append(user)
    return result


def make_projects(h, count=10, tracks=3):
    result = []
    for number in range(tracks):
        track = Track.objects.create(hackathon=h, name=f'赛道{number}', state='published')
        for index in range(count):
            user = User.objects.create_user(email=f'team{number}-{index}@example.invalid')
            person = Participant.objects.create(hackathon=h, user=user, name=f'队长{number}-{index}', consent=True)
            team = Team.objects.create(hackathon=h, captain=person, name=f'队伍{number}-{index}', track=track)
            person.team = team
            person.save()
            result.append(
                Project.objects.create(
                    hackathon=h,
                    team=team,
                    title=f'作品{number}-{index}',
                    state='approved',
                    submitted_at=timezone.now(),
                    final_material_kind='slides',
                )
            )
    return result


def capture(h, actor, projects):
    return SubmissionSnapshot.objects.create(
        hackathon=h,
        version=1,
        captured_at=timezone.now(),
        captured_by=actor,
        reason='合成测试资格快照',
        manifest={
            'entries': [
                {
                    'project_id': str(project.pk),
                    'team_id': str(project.team_id),
                    'eligibility': 'ready',
                    'final_material_kind': 'slides',
                    'assets': [],
                }
                for project in projects
            ]
        },
        sha256='a' * 64,
    )


def rules(kind, lead):
    return {
        'criteria': [{'key': key, 'label': label, 'weight': weight} for key, label, weight in ROUND_CRITERIA[kind]],
        'aggregation': 'mean',
        'trim': 0,
        'minimum_judges': 2 if kind == 'preliminary' else 6,
        'minimum_nonstudent': 0 if kind == 'preliminary' else 6,
        'reviewers': [{'user_id': str(lead.pk), 'name': lead.fullname, 'tracks': [], 'nonstudent': True}],
    }


def freeze(h, lead, judges, kind='preliminary', track_mapping=None):
    return workflow.create_round(
        h,
        lead,
        kind=kind,
        name=kind,
        rules=rules(kind, lead),
        panel=[
            {
                'user_id': str(judge.pk),
                'tracks': track_mapping.get(judge.pk, []) if track_mapping else [],
                'nonstudent': kind == 'final',
            }
            for judge in judges
        ],
        signer=lead,
        signer_nonstudent=True,
    )


def fill(h, round, judges, projects, value=8):
    for project in projects:
        for judge in judges:
            if str(judge.pk) not in workflow.task_judges(round, project.pk):
                continue
            workflow.save_round_score(
                h,
                judge,
                round,
                project,
                values={
                    criterion['key']: value if not callable(value) else value(project)
                    for criterion in round.rules['criteria']
                },
                final=True,
                revision=0,
            )


def shortlist(h, lead, round, seats=8):
    workflow.set_round_open(h, lead, round, False)
    selections = {
        track_id: [row['project_id'] for row in workflow.round_results(round) if row['track_id'] == track_id]
        for track_id in {row['track_id'] for row in round.tasks for row in [round.materials[row['project_id']]]}
    }
    result = workflow.make_snapshot(
        h,
        lead,
        round,
        kind='finalists',
        reason='同分复核并确认名额',
        seats={key: seats for key in selections},
        alternates=2,
        selections=selections,
    )
    return workflow.sign_snapshot(h, lead, result, result.sha256)


def endpoint(h, name, **kwargs):
    return reverse('hackathon:workflow-' + name, kwargs={'hackathon_slug': h.slug, **kwargs})


def test_three_track_frozen_tasks_shortlist_queue_and_round_isolation(hackathon, organizer_user):
    projects = make_projects(hackathon)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon, 6)
    tracks = list(Track.objects.filter(hackathon=hackathon))
    mapping = {judge.pk: [str(tracks[index // 2].pk)] for index, judge in enumerate(judges)}
    first = freeze(hackathon, organizer_user, judges, track_mapping=mapping)
    assert all(sum(str(judge.pk) in task['judges'] for task in first.tasks) == 10 for judge in judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges, projects)
    signed = shortlist(hackathon, organizer_user, first)
    assert len(workflow.signed_entries(signed)) == 24
    assert len(workflow.signed_entries(signed, 'alternate')) == 6
    workflow.apply_finalist_queue(hackathon, organizer_user, signed)
    workflow.apply_finalist_queue(hackathon, organizer_user, signed)
    assert DemoSlot.objects.filter(hackathon=hackathon).count() == 24
    assert Project.objects.filter(hackathon=hackathon, state='approved').count() == 30
    final = freeze(hackathon, organizer_user, judges, kind='final')
    assert len(final.tasks) == 24
    workflow.set_round_open(hackathon, organizer_user, final, True)
    final_project = Project.objects.get(pk=workflow.signed_entries(signed)[0]['project_id'])
    workflow.save_round_score(
        hackathon,
        judges[0],
        final,
        final_project,
        values={c['key']: 2 for c in final.rules['criteria']},
        final=True,
        revision=0,
    )
    assert RoundScore.objects.filter(round=first).count() == 60
    assert RoundScore.objects.filter(round=final).count() == 1
    assert workflow.round_results(final)[0]['complete'] is False
    assert Score.objects.filter(hackathon=hackathon).count() == 0


def test_round_permission_revision_and_independent_reviewer(hackathon, organizer_user, user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    invalid = rules('preliminary', judges[0])
    with pytest.raises(ValidationError, match='独立非学生复核'):
        workflow.create_round(
            hackathon,
            organizer_user,
            kind='preliminary',
            name='bad',
            rules=invalid,
            panel=[{'user_id': str(j.pk), 'tracks': [], 'nonstudent': False} for j in judges],
            signer=organizer_user,
            signer_nonstudent=True,
        )
    first = freeze(hackathon, organizer_user, judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    values = {c['key']: 9 for c in first.rules['criteria']}
    with pytest.raises(PermissionDenied):
        workflow.save_round_score(hackathon, user, first, projects[0], values=values, final=True, revision=0)
    workflow.save_round_score(hackathon, judges[0], first, projects[0], values=values, final=True, revision=0)
    with pytest.raises(ValidationError, match='评分已更新'):
        workflow.save_round_score(hackathon, judges[0], first, projects[0], values=values, final=True, revision=0)
    first.name = 'silently changed'
    with pytest.raises(ValidationError, match='已冻结'):
        first.save()


def test_missing_judge_stays_missing_and_tie_requires_review(hackathon, organizer_user):
    projects = make_projects(hackathon, count=2, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    first = freeze(hackathon, organizer_user, judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges[:1], projects)
    StaffAssignment.objects.filter(hackathon=hackathon, user=judges[1]).delete()
    judges[1].is_active = False
    judges[1].save()
    workflow.set_round_open(hackathon, organizer_user, first, False)
    seats = {str(projects[0].team.track_id): 1}
    with pytest.raises(ValidationError, match='仍有缺评'):
        workflow.make_snapshot(hackathon, organizer_user, first, kind='finalists', reason='test', seats=seats)
    assert workflow.round_results(first)[0]['expected'] == 2
    judges[1].is_active = True
    judges[1].save()
    StaffAssignment.objects.create(hackathon=hackathon, user=judges[1], role='judge')
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges[1:], projects)
    workflow.set_round_open(hackathon, organizer_user, first, False)
    with pytest.raises(ValidationError, match='边界同分'):
        workflow.make_snapshot(hackathon, organizer_user, first, kind='finalists', reason='test', seats=seats)


def test_signer_only_immutable_stale_snapshot_and_exact_source(hackathon, organizer_user, user):
    projects = make_projects(hackathon, count=1, tracks=1)
    original = capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    first = freeze(hackathon, organizer_user, judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges, projects)
    workflow.set_round_open(hackathon, organizer_user, first, False)
    args = dict(kind='finalists', reason='明确依据', seats={str(projects[0].team.track_id): 1})
    older = workflow.make_snapshot(hackathon, organizer_user, first, **args)
    newer = workflow.make_snapshot(hackathon, organizer_user, first, **args)
    with pytest.raises(ValidationError, match='名单已更新'):
        workflow.sign_snapshot(hackathon, organizer_user, older, older.sha256)
    with pytest.raises(PermissionDenied):
        workflow.sign_snapshot(hackathon, user, newer, newer.sha256)
    workflow.sign_snapshot(hackathon, organizer_user, newer, newer.sha256)
    assert newer.manifest['material_snapshot_id'] == str(original.pk)
    newer.reason = 'overwrite'
    with pytest.raises(ValidationError, match='不可覆盖'):
        newer.save()
    with pytest.raises(ValidationError, match='不能重新开放'):
        workflow.set_round_open(hackathon, organizer_user, first, True)


def test_explicit_notification_windows_and_stale_captain_response(hackathon, organizer_user):
    projects = make_projects(hackathon, count=2, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    first = freeze(hackathon, organizer_user, judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges, projects)
    signed = shortlist(hackathon, organizer_user, first, seats=1)
    contact = FinalistContact.objects.get(snapshot=signed)
    now = timezone.now()
    contact = workflow.update_contact(
        hackathon,
        organizer_user,
        contact,
        revision=contact.revision,
        state='sent',
        channel='人工电话',
        sent_at=now,
        deadline=now + timedelta(days=1),
        note='本人已送达',
    )
    assert contact.deadline == now + timedelta(minutes=15)
    contact = workflow.update_contact(
        hackathon,
        organizer_user,
        contact,
        revision=contact.revision,
        state='declined',
        channel=contact.channel,
        sent_at=contact.sent_at,
        deadline=contact.deadline,
        note='队长电话明确无法参加',
    )
    outgoing = workflow.signed_entries(signed)[0]
    incoming = workflow.signed_entries(signed, 'alternate')[0]
    promotion = workflow.promote_alternate(
        hackathon, organizer_user, signed, outgoing['project_id'], incoming['project_id'], '同赛道候补递补，已电话核实'
    )
    workflow.sign_snapshot(hackathon, organizer_user, promotion, promotion.sha256)
    with pytest.raises(ValidationError, match='版本已失效'):
        workflow.captain_confirm(hackathon, contact.team.captain.user, signed)
    replacement = FinalistContact.objects.get(snapshot=promotion)
    replacement = workflow.update_contact(
        hackathon,
        organizer_user,
        replacement,
        revision=replacement.revision,
        state='sent',
        channel='电话',
        sent_at=now,
        note='已送达候补',
    )
    assert replacement.deadline == now + timedelta(minutes=5)
    workflow.captain_confirm(hackathon, replacement.team.captain.user, promotion)
    workflow.captain_confirm(hackathon, replacement.team.captain.user, promotion)
    assert FinalistContact.objects.filter(snapshot=promotion, state='accepted').count() == 1


def test_score_form_and_browser_entry_smoke(client, hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    first = freeze(hackathon, organizer_user, judges)
    form = RoundScoreForm(
        {'revision': 0, 'fit': 10, 'function': 9, 'value': 8, 'clarity': 7}, round=first, confirm=True
    )
    assert form.is_valid(), form.errors
    assert list(form.values()) == ['fit', 'function', 'value', 'clarity']
    client.force_login(organizer_user)
    assert client.get(endpoint(hackathon, 'manage')).status_code == 200
    assert client.get(endpoint(hackathon, 'round', round_id=first.pk)).status_code == 200
    client.force_login(judges[0])
    assert client.get(endpoint(hackathon, 'judging')).status_code == 200
    assert client.get(endpoint(hackathon, 'tasks', round_id=first.pk)).status_code == 200
    response = client.get(endpoint(hackathon, 'score', round_id=first.pk, project_id=projects[0].pk))
    assert response.status_code == 200
    client.force_login(projects[0].team.captain.user)
    assert client.get(endpoint(hackathon, 'score', round_id=first.pk, project_id=projects[0].pk)).status_code == 403


def test_legacy_results_cannot_publish_without_signed_awards(hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    freeze(hackathon, organizer_user, judges)
    assert services.results(hackathon) == []
    with pytest.raises(ValidationError, match='总负责人签认'):
        services.set_publish_flag(hackathon, organizer_user, 'publish-results')
    with pytest.raises(ValidationError, match='先签认入围'):
        services.generate_queue(hackathon, organizer_user)


def test_signed_awards_are_authoritative_and_public_toggle_does_not_unlock(client, hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon, 6)
    first = freeze(hackathon, organizer_user, judges[:2])
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges[:2], projects)
    shortlist(hackathon, organizer_user, first)
    final = freeze(hackathon, organizer_user, judges, kind='final')
    workflow.set_round_open(hackathon, organizer_user, final, True)
    fill(hackathon, final, judges, projects, value=9)
    workflow.set_round_open(hackathon, organizer_user, final, False)
    snapshot = workflow.make_snapshot(
        hackathon, organizer_user, final, kind='awards', reason='最终名单复核', awards={str(projects[0].pk): '一等奖'}
    )
    workflow.sign_snapshot(hackathon, organizer_user, snapshot, snapshot.sha256)
    services.set_publish_flag(hackathon, organizer_user, 'publish-results')
    Project.objects.filter(pk=projects[0].pk).update(award='untrusted edit')
    assert services.results(hackathon)[0]['project'].award == '一等奖'
    client.force_login(organizer_user)
    results_url = reverse('hackathon:results', kwargs={'hackathon_slug': hackathon.slug})
    body = client.get(results_url).content.decode()
    assert '现场演示及完成度 35%' in body
    assert '答问与表达 15%' in body
    export_url = reverse('hackathon:export', kwargs={'hackathon_slug': hackathon.slug, 'kind': 'results'})
    exported = client.get(export_url).content.decode('utf-8-sig')
    assert '现场演示及完成度 35%' in exported
    assert str(snapshot.pk) in exported
    assert snapshot.sha256 in exported
    assert '一等奖' in exported and 'untrusted edit' not in exported
    services.set_publish_flag(hackathon, organizer_user, 'unpublish-results')
    with pytest.raises(ValidationError, match='不能重新开放'):
        workflow.set_round_open(hackathon, organizer_user, final, True)
    assert workflow.current_signed_snapshot(hackathon, 'awards').pk == snapshot.pk


def test_reviewer_read_only_track_scope_and_immutable_media(client, monkeypatch, hackathon, organizer_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from eventyay.hackathon import workflow_views
    from eventyay.hackathon.models import Asset

    projects = make_projects(hackathon, count=1, tracks=2)
    judges = make_judges(hackathon, 3)
    reviewer = judges.pop()
    materials = []
    for index, project in enumerate(projects):
        asset = Asset.objects.create(
            hackathon=hackathon,
            project=project,
            kind='preliminary',
            file=SimpleUploadedFile(f'short{index}.mp4', b'synthetic-video'),
            filename=f'short{index}.mp4',
            checksum='b' * 64,
            size=15,
        )
        materials.append(
            {
                'project_id': str(project.pk),
                'team_id': str(project.team_id),
                'eligibility': 'ready',
                'final_material_kind': 'slides',
                'assets': [
                    {
                        'id': str(asset.pk),
                        'kind': asset.kind,
                        'filename': asset.filename,
                        'storage_name': asset.file.name,
                        'size': asset.size,
                        'sha256': asset.checksum,
                    }
                ],
            }
        )
    SubmissionSnapshot.objects.create(
        hackathon=hackathon,
        version=1,
        captured_at=timezone.now(),
        captured_by=organizer_user,
        manifest={'entries': materials},
        sha256='d' * 64,
        reason='synthetic',
    )
    config = rules('preliminary', organizer_user)
    config['reviewers'] = [
        {'user_id': str(reviewer.pk), 'tracks': [str(projects[0].team.track_id)], 'nonstudent': True},
        {'user_id': str(organizer_user.pk), 'tracks': [str(projects[1].team.track_id)], 'nonstudent': True},
    ]
    round = workflow.create_round(
        hackathon,
        organizer_user,
        kind='preliminary',
        name='初评',
        rules=config,
        panel=[{'user_id': str(judge.pk), 'tracks': [], 'nonstudent': False} for judge in judges],
        signer=organizer_user,
        signer_nonstudent=True,
    )
    workflow.set_round_open(hackathon, organizer_user, round, True)
    monkeypatch.setattr(
        workflow_views,
        'private_media_source',
        lambda storage, name, filename, fallback: {'url': fallback, 'transport': 'relay', 'expires_in': 300},
    )
    client.force_login(reviewer)
    assert client.get(endpoint(hackathon, 'tasks', round_id=round.pk)).status_code == 200
    own = endpoint(hackathon, 'media-source', round_id=round.pk, project_id=projects[0].pk, kind='preliminary')
    other = endpoint(hackathon, 'media-source', round_id=round.pk, project_id=projects[1].pk, kind='preliminary')
    assert client.get(own).status_code == 200
    assert client.get(other).status_code == 403
    score_url = endpoint(hackathon, 'score', round_id=round.pk, project_id=projects[0].pk)
    response = client.get(score_url)
    assert response.status_code == 200
    assert '确认本轮评分并返回任务' not in response.content.decode()
    assert '复核页面，只读' in response.content.decode()
    assert '本轮评分窗口开放中' in response.content.decode()
    assert '本轮窗口已关闭' not in response.content.decode()
    assert (
        client.post(
            score_url, {'action': 'final', 'revision': 0, 'fit': 1, 'function': 1, 'value': 1, 'clarity': 1}
        ).status_code
        == 403
    )
    asset = projects[0].assets.get(kind='preliminary')
    assert workflow.can_judge_material(hackathon, reviewer, projects[0].pk, kind='preliminary', asset=asset)
    assert not workflow.can_judge_material(
        hackathon, reviewer, projects[1].pk, kind='preliminary', asset=projects[1].assets.get()
    )
    asset.file.name = 'replacement-different-storage.mp4'
    assert not workflow.can_judge_material(hackathon, reviewer, projects[0].pk, kind='preliminary', asset=asset)
    assert client.get(own).status_code == 200


def test_legacy_page_cannot_write_after_round_freeze(hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    legacy = Score.objects.create(
        hackathon=hackathon,
        project=projects[0],
        judge=judges[0],
        innovation=1,
        implementation=1,
        impact=1,
        presentation=1,
        final=True,
    )
    freeze(hackathon, organizer_user, judges)
    with pytest.raises(ValidationError, match='按轮次评审'):
        services.save_score(hackathon, judges[0], projects[0], None, confirm=True)
    legacy.refresh_from_db()
    assert legacy.innovation == 1
    assert RoundScore.objects.filter(hackathon=hackathon).count() == 0


def test_closed_final_round_correction_preserves_old_scores_and_blocks_old_award_publish(hackathon, organizer_user):
    projects = make_projects(hackathon, count=2, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon, 6)
    first = freeze(hackathon, organizer_user, judges[:2])
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges[:2], projects)
    signed = shortlist(hackathon, organizer_user, first, seats=1)
    final = freeze(hackathon, organizer_user, judges, kind='final')
    workflow.set_round_open(hackathon, organizer_user, final, True)
    finalist = Project.objects.get(pk=workflow.signed_entries(signed)[0]['project_id'])
    fill(hackathon, final, judges, [finalist])
    workflow.set_round_open(hackathon, organizer_user, final, False)
    awards = workflow.make_snapshot(
        hackathon, organizer_user, final, kind='awards', reason='演练签认', awards={str(finalist.pk): '演练奖'}
    )
    workflow.sign_snapshot(hackathon, organizer_user, awards, awards.sha256)
    contact = FinalistContact.objects.get(snapshot=signed)
    workflow.update_contact(
        hackathon, organizer_user, contact, revision=contact.revision, state='declined', note='队长电话明确退出'
    )
    alternate = workflow.signed_entries(signed, 'alternate')[0]
    promoted = workflow.promote_alternate(
        hackathon, organizer_user, signed, str(finalist.pk), alternate['project_id'], '正式前更正演练名单'
    )
    workflow.sign_snapshot(hackathon, organizer_user, promoted, promoted.sha256)
    with pytest.raises(ValidationError, match='当前入围版本'):
        services.set_publish_flag(hackathon, organizer_user, 'publish-results')
    new_final = freeze(hackathon, organizer_user, judges, kind='final')
    assert new_final.version == 2
    assert new_final.source_snapshot_id == promoted.pk
    assert RoundScore.objects.filter(round=final).count() == 6
    assert RoundScore.objects.filter(round=new_final).count() == 0
    with pytest.raises(ValidationError, match='已有新版本'):
        workflow.set_round_open(hackathon, organizer_user, final, True)


def test_changed_scores_after_draft_require_new_snapshot(hackathon, organizer_user):
    projects = make_projects(hackathon, count=1, tracks=1)
    capture(hackathon, organizer_user, projects)
    judges = make_judges(hackathon)
    first = freeze(hackathon, organizer_user, judges)
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges, projects)
    workflow.set_round_open(hackathon, organizer_user, first, False)
    draft = workflow.make_snapshot(
        hackathon, organizer_user, first, kind='finalists', reason='待复核', seats={str(projects[0].team.track_id): 1}
    )
    workflow.set_round_open(hackathon, organizer_user, first, True)
    workflow.save_round_score(
        hackathon,
        judges[0],
        first,
        projects[0],
        values={criterion['key']: 9 for criterion in first.rules['criteria']},
        final=True,
        revision=1,
    )
    workflow.set_round_open(hackathon, organizer_user, first, False)
    with pytest.raises(ValidationError, match='评分发生变化'):
        workflow.sign_snapshot(hackathon, organizer_user, draft, draft.sha256)


def test_station_bearer_uses_only_selected_frozen_final_material(client, hackathon, organizer_user):
    import hashlib

    from django.core.files.uploadedfile import SimpleUploadedFile

    from eventyay.hackathon.models import Asset, StationToken

    projects = make_projects(hackathon, count=2, tracks=1)
    original = b'%PDF-1.4\nfrozen-selected-file\n'
    selected = Asset.objects.create(
        hackathon=hackathon,
        project=projects[0],
        kind='slides',
        file=SimpleUploadedFile('final.pdf', original),
        filename='final.pdf',
        checksum=hashlib.sha256(original).hexdigest(),
        size=len(original),
    )
    extra = Asset.objects.create(
        hackathon=hackathon,
        project=projects[0],
        kind='video',
        file=SimpleUploadedFile('extra.mp4', b'not-selected'),
        filename='extra.mp4',
        checksum='e' * 64,
        size=12,
    )
    materials = []
    for project in projects:
        materials.append(
            {
                'project_id': str(project.pk),
                'team_id': str(project.team_id),
                'eligibility': 'ready',
                'final_material_kind': 'slides',
                'assets': [
                    {
                        'id': str(a.pk),
                        'kind': a.kind,
                        'filename': a.filename,
                        'storage_name': a.file.name,
                        'size': a.size,
                        'sha256': a.checksum,
                    }
                    for a in project.assets.all()
                ],
            }
        )
    SubmissionSnapshot.objects.create(
        hackathon=hackathon,
        version=1,
        captured_at=timezone.now(),
        captured_by=organizer_user,
        manifest={'entries': materials},
        sha256='a' * 64,
        reason='synthetic',
    )
    judges = make_judges(hackathon, 6)
    first = freeze(hackathon, organizer_user, judges[:2])
    token = 'synthetic-station-material-token'
    StationToken.objects.create(
        hackathon=hackathon,
        digest=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(hours=1),
        created_by=organizer_user,
    )
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
    state_url = reverse('hackathon:station-state', kwargs={'hackathon_slug': hackathon.slug})
    selected_url = reverse(
        'hackathon:station-asset', kwargs={'hackathon_slug': hackathon.slug, 'asset_id': selected.pk}
    )
    assert client.get(state_url, **headers).status_code == 409
    assert client.get(selected_url, **headers).status_code == 409
    workflow.set_round_open(hackathon, organizer_user, first, True)
    fill(hackathon, first, judges[:2], projects, value=lambda p: 9 if p.pk == projects[0].pk else 8)
    signed = shortlist(hackathon, organizer_user, first, seats=1)
    workflow.apply_finalist_queue(hackathon, organizer_user, signed)
    final = freeze(hackathon, organizer_user, judges, kind='final')
    frozen_name, frozen_checksum = selected.file.name, selected.checksum
    selected.file = SimpleUploadedFile('changed.pdf', b'new-current-replacement')
    selected.checksum = 'd' * 64
    selected.size = 23
    selected.save()
    response = client.get(state_url, **headers)
    assert response.status_code == 200
    payload = response.json()
    assert [row['id'] for row in payload['assets']] == [str(selected.pk)]
    assert payload['assets'][0]['sha256'] == frozen_checksum
    assert payload['assets'][0]['round_id'] == str(final.pk)
    response = client.get(selected_url, **headers)
    assert response.status_code == 200
    assert b''.join(response.streaming_content) == original
    extra_url = reverse('hackathon:station-asset', kwargs={'hackathon_slug': hackathon.slug, 'asset_id': extra.pk})
    assert client.get(extra_url, **headers).status_code == 404
    assert selected.file.name != frozen_name
