"""Transactional commands for the two-round judging and signed-list workflow."""

import hashlib
import json
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from eventyay.hackathon import services
from eventyay.hackathon.models import DemoSlot, Participant, Project, StaffAssignment
from eventyay.hackathon.permissions import is_organizer
from eventyay.hackathon.workflow_models import FinalistContact, JudgingRound, ResultSnapshot, RoundScore


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def require_organizer(hackathon, user):
    if not is_organizer(user, hackathon):
        raise PermissionDenied


def current_signed_snapshot(hackathon, kind='finalists'):
    return (
        ResultSnapshot.objects.filter(hackathon=hackathon, kind=kind, signed_at__isnull=False)
        .order_by('-version')
        .first()
    )


def signed_entries(snapshot, status='finalist'):
    return [row for row in snapshot.manifest['entries'] if row['status'] == status]


def materials_for(project):
    return {
        'project_id': str(project.pk),
        'team_id': str(project.team_id),
        'team': project.team.name,
        'title': project.title,
        'code': project.code,
        'team_code': project.team.code,
        'track_id': str(project.team.track_id or ''),
        'track': project.team.track.name if project.team.track_id else '未选赛道',
        'captain': project.team.captain.name,
        'revision': project.revision,
        'receipt': str(project.submission_receipt or ''),
        'manifest_hash': project.submission_manifest_hash,
        'final_material_kind': getattr(project, 'final_material_kind', ''),
        'assets': [
            {
                'id': str(asset.pk),
                'kind': asset.kind,
                'filename': asset.filename,
                'storage_name': asset.file.name,
                'checksum': asset.checksum,
                'size': asset.size,
            }
            for asset in project.assets.order_by('kind')
        ],
    }


def validate_rules(rules):
    criteria = rules.get('criteria', [])
    if not criteria or len(criteria) > 10:
        raise ValidationError('请提供1至10项评分指标。')
    keys = [row.get('key') for row in criteria]
    if len(set(keys)) != len(keys) or any(not isinstance(key, str) or not key.isidentifier() for key in keys):
        raise ValidationError('评分指标标识必须唯一。')
    if any(
        not row.get('label') or type(row.get('weight')) is not int or not 0 <= row['weight'] <= 100 for row in criteria
    ):
        raise ValidationError('指标名称不能为空，权重须为0至100的整数。')
    if sum(row['weight'] for row in criteria) != 100:
        raise ValidationError('各指标权重之和必须为100%。')
    if (
        rules.get('aggregation') not in {'mean', 'trimmed'}
        or type(rules.get('trim')) is not int
        or not 0 <= rules['trim'] <= 10
    ):
        raise ValidationError('请核对算分方法和去极值份数。')
    if type(rules.get('minimum_judges')) is not int or not 1 <= rules['minimum_judges'] <= 100:
        raise ValidationError('请设置每队至少需要的有效评委数。')
    if (
        type(rules.get('minimum_nonstudent')) is not int
        or not 0 <= rules['minimum_nonstudent'] <= rules['minimum_judges']
    ):
        raise ValidationError('非学生评委门槛不能超过有效评委数。')


@transaction.atomic
def create_round(hackathon, user, *, kind, name, rules, panel, signer, signer_nonstudent):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    if h.results_published:
        raise ValidationError('成绩已公布，不能新增评分轮次。')
    if kind not in JudgingRound.Kind.values:
        raise ValidationError('未知评审轮次。')
    if JudgingRound.objects.filter(hackathon=h, kind=kind, is_open=True).exists():
        raise ValidationError('请先关闭该阶段已开放的旧轮次。')
    validate_rules(rules)
    if not signer_nonstudent or not signer.is_active or signer.is_spam:
        raise ValidationError('请指定已核实身份的非学生总负责人签认名单。')
    planned = rules.get('source') == 'plan'
    # A competition plan states its own panel sizes; manual rounds keep the documented floors.
    minimum = 2 if kind == 'preliminary' else (1 if planned else 6)
    nonstudent = 0 if kind == 'preliminary' or planned else 6
    if rules['minimum_judges'] < minimum or rules['minimum_nonstudent'] < nonstudent:
        raise ValidationError('评委门槛低于本届方案：初评每道2人含非学生复核；决赛至少6名非学生评委。')
    if kind == 'final' and any(row['tracks'] for row in panel):
        raise ValidationError('决赛使用同一评委组覆盖所有入围队伍，请将赛道范围留空。')
    assigned = {
        str(a.user_id): a
        for a in StaffAssignment.objects.filter(
            hackathon=h, role='judge', user__is_active=True, user__is_spam=False
        ).select_related('user')
    }
    if not panel or len({row['user_id'] for row in panel}) != len(panel):
        raise ValidationError('评委名单不能为空或重复。')
    for row in panel:
        if row['user_id'] not in assigned or type(row.get('nonstudent')) is not bool:
            raise ValidationError('请只选择本活动的可用评委，并确认人员资格。')
        row['name'] = assigned[row['user_id']].user.get_display_name()
    source = current_signed_snapshot(h, 'finalists') if kind == 'final' else None
    if kind == 'final' and not source:
        raise ValidationError('请先签认入围名单，再建立决赛轮次。')
    projects = (
        Project.objects.filter(hackathon=h, state='approved')
        .select_related('team__track', 'team__captain')
        .prefetch_related('assets')
    )
    if source:
        projects = projects.filter(pk__in=[row['project_id'] for row in signed_entries(source)])
    elif planned:
        # A planned preliminary judges exactly what the frozen snapshot found ready; the rest stay listed as issues.
        from eventyay.hackathon.models import SubmissionSnapshot

        frozen = SubmissionSnapshot.objects.filter(hackathon=h).order_by('-version').first()
        entries = frozen.manifest['entries'] if frozen else []
        ready = [row['project_id'] for row in entries if row['eligibility'] == 'ready']
        projects = projects.filter(pk__in=ready)
    projects = list(projects.order_by('team__track_id', 'pk'))
    if not projects:
        raise ValidationError('没有可供本轮评审的已通过作品。')
    if source and len(projects) != len(signed_entries(source)):
        raise ValidationError('入围作品状态已变化，请复核材料资格后再冻结。')
    conflicts = {(str(p.user_id), str(p.team_id)) for p in Participant.objects.filter(hackathon=h, team__isnull=False)}
    tasks, materials = [], {}
    for project in projects:
        judges = [
            row
            for row in panel
            if (not row['tracks'] or str(project.team.track_id) in row['tracks'])
            and (row['user_id'], str(project.team_id)) not in conflicts
        ]
        if (
            len(judges) < rules['minimum_judges']
            or sum(row['nonstudent'] for row in judges) < rules['minimum_nonstudent']
        ):
            raise ValidationError(
                f'「{project.team.name}」的有效评委或非学生评委不足，已自动排除本队成员；本轮未建立。'
            )
        if (
            kind == 'preliminary'
            and not planned
            and not any(
                row.get('nonstudent') is True
                and (row['user_id'], str(project.team_id)) not in conflicts
                and row['user_id'] not in {judge['user_id'] for judge in judges}
                and (not row['tracks'] or str(project.team.track_id) in row['tracks'])
                for row in rules.get('reviewers', [])
            )
        ):
            raise ValidationError(f'「{project.team.name}」缺少独立非学生复核人；复核人不能兼任该赛道打分。')
        tasks.append({'project_id': str(project.pk), 'judges': [row['user_id'] for row in judges]})
        materials[str(project.pk)] = materials_for(project)
    from eventyay.hackathon.models import SubmissionSnapshot

    material_snapshot = SubmissionSnapshot.objects.filter(hackathon=h).order_by('-version').first()
    if not material_snapshot:
        raise ValidationError('请先在材料预检生成资格与材料快照。')
    material_rows = {row['project_id']: row for row in material_snapshot.manifest['entries']}
    for project_id, material in materials.items():
        frozen = material_rows.get(project_id)
        if not frozen or frozen.get('eligibility') != 'ready':
            raise ValidationError('本轮包含未完成材料资格预检的作品，请核对当前资格快照。')
        material['assets'] = frozen['assets']
        material['final_material_kind'] = frozen.get('final_material_kind', '')
    rules = dict(
        rules, material_snapshot_id=str(material_snapshot.pk), material_snapshot_sha256=material_snapshot.sha256
    )
    version = (JudgingRound.objects.filter(hackathon=h, kind=kind).aggregate(v=Max('version'))['v'] or 0) + 1
    result = JudgingRound.objects.create(
        hackathon=h,
        kind=kind,
        version=version,
        name=name,
        rules=rules,
        panel=panel,
        tasks=tasks,
        materials=materials,
        source_snapshot=source,
        signer=signer,
        signer_nonstudent=signer_nonstudent,
        frozen_at=timezone.now(),
        frozen_by=user,
    )
    services.audit(h, user, '冻结评审轮次', str(result.pk), f'{name} v{version}；{len(tasks)}队；{len(panel)}名评委')
    return result


def task_judges(round, project_id):
    return next((row['judges'] for row in round.tasks if row['project_id'] == str(project_id)), [])


@transaction.atomic
def set_round_open(hackathon, user, round, opening):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    current = JudgingRound.objects.select_for_update().get(hackathon=h, pk=round.pk)
    if opening:
        latest = JudgingRound.objects.filter(hackathon=h, kind=current.kind).order_by('-version').first()
        if latest.pk != current.pk:
            raise ValidationError('此轮次已有新版本，请使用最新版本。')
        if h.results_published or current.snapshots.filter(signed_at__isnull=False).exists():
            raise ValidationError('已签认或公布的轮次不能重新开放；更正请建立新版本。')
        if current.kind == 'final' and current.source_snapshot_id != getattr(current_signed_snapshot(h), 'pk', None):
            raise ValidationError('入围名单已更新，请按新名单建立决赛轮次。')
        if JudgingRound.objects.filter(hackathon=h, kind=current.kind, is_open=True).exclude(pk=current.pk).exists():
            raise ValidationError('同一阶段只能开放一个版本。')
    current.is_open = opening
    current.save(update_fields=['is_open', 'updated'])
    services.audit(h, user, '开放评审轮次' if opening else '关闭评审轮次', str(current.pk))
    return current


def criterion_max(rules, criterion):
    """Full marks of one dimension: its weight on the points scale, 10 on the legacy 0–10 scale."""
    return criterion['weight'] if rules.get('scale') == 'points' else 10


def score_total(rules, values):
    if rules.get('scale') == 'points':
        return sum(Decimal(values[c['key']]) for c in rules['criteria'])
    return sum(Decimal(values[c['key']]) * c['weight'] / 10 for c in rules['criteria'])


@transaction.atomic
def save_round_score(hackathon, user, round, project, *, values, final, revision, feedback=''):
    h = services.lock_event(hackathon)
    current = JudgingRound.objects.select_for_update().get(hackathon=h, pk=round.pk)
    if (
        not user.is_active
        or user.is_spam
        or not StaffAssignment.objects.filter(hackathon=h, user=user, role='judge').exists()
        or str(user.pk) not in task_judges(current, project.pk)
    ):
        raise PermissionDenied
    if current.kind == 'final' and current.source_snapshot_id != getattr(current_signed_snapshot(h), 'pk', None):
        raise ValidationError('入围名单已更新，请打开按新名单建立的决赛轮次。')
    if not current.is_open or h.results_published or current.snapshots.filter(signed_at__isnull=False).exists():
        raise ValidationError('本轮评分窗口已关闭，本次输入未保存。')
    if (
        project.hackathon_id != h.pk
        or Participant.objects.filter(hackathon=h, team_id=project.team_id, user=user).exists()
    ):
        raise PermissionDenied
    expected = {row['key'] for row in current.rules['criteria']}
    limits = {row['key']: criterion_max(current.rules, row) for row in current.rules['criteria']}
    if set(values) != expected or any(
        value is not None and (type(value) is not int or not 0 <= value <= limits[key]) for key, value in values.items()
    ):
        raise ValidationError('各项分数须为 0 至该项满分之间的整数。')
    if final and any(value is None for value in values.values()):
        raise ValidationError('请填全各项分数后确认；缺项不会作为0分计入。')
    score = RoundScore.objects.filter(round=current, project=project, judge=user).first()
    if revision != (score.revision if score else 0):
        raise ValidationError('此评分已更新，请重新打开本轮页面核对；本次没有覆盖新评分。')
    score = score or RoundScore(hackathon=h, round=current, project=project, judge=user, revision=0)
    score.values, score.final, score.feedback = values, final, feedback
    score.revision += 1
    score.save()
    services.audit(
        h, user, '确认轮次评分' if final else '保存轮次草稿', project.code, f'{current.name} v{current.version}'
    )
    return score


def round_results(round):
    scores = {
        (str(s.project_id), str(s.judge_id)): s
        for s in RoundScore.objects.filter(hackathon=round.hackathon, round=round)
    }
    rows = []
    for task in round.tasks:
        row = dict(round.materials[task['project_id']])
        totals, missing, source = [], [], []
        for judge_id in task['judges']:
            score = scores.get((task['project_id'], judge_id))
            if (
                score is None
                or not score.final
                or any(score.values.get(c['key']) is None for c in round.rules['criteria'])
            ):
                missing.append(judge_id)
                continue
            totals.append(score_total(round.rules, score.values))
            source.append({'judge_id': judge_id, 'revision': score.revision, 'values': score.values})
        totals.sort()
        trim = (
            round.rules['trim']
            if round.rules['aggregation'] == 'trimmed' and len(totals) > 2 * round.rules['trim']
            else 0
        )
        counted = totals[trim : len(totals) - trim] if trim else totals
        exact = Fraction(sum(counted)) / len(counted) if counted else None
        # Rank on the exact mean; two decimals are for display only, so rounding never creates a tie.
        average = (
            str(
                (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            )
            if counted
            else None
        )
        row.update(
            average=average,
            exact=str(exact) if counted else None,
            complete=not missing and bool(task['judges']),
            missing=missing,
            expected=len(task['judges']),
            confirmed=len(totals),
            scores=source,
        )
        rows.append(row)
    rows.sort(key=lambda r: (r['track_id'], not r['complete'], -Fraction(r['exact'] or 0), r['project_id']))
    previous, rank, track_index, track = None, 0, 0, None
    for row in rows:
        if row['track_id'] != track:
            previous, rank, track_index, track = None, 0, 0, row['track_id']
        track_index += 1
        if row['complete'] and row['exact'] != previous:
            rank = track_index
        row['rank'] = rank if row['complete'] else None
        previous = row['exact']
    return rows


@transaction.atomic
def make_snapshot(hackathon, user, round, *, kind, reason, seats=None, alternates=0, selections=None, awards=None):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    current = JudgingRound.objects.get(hackathon=h, pk=round.pk)
    if (kind, current.kind) not in {('finalists', 'preliminary'), ('awards', 'final')}:
        raise ValidationError('初评生成入围名单，决赛生成获奖名单。')
    latest_round = JudgingRound.objects.filter(hackathon=h, kind=current.kind).order_by('-version').first()
    if latest_round.pk != current.pk or (
        current.kind == 'final' and current.source_snapshot_id != getattr(current_signed_snapshot(h), 'pk', None)
    ):
        raise ValidationError('轮次或入围名单已有新版本，请复核最新轮次。')
    if current.is_open:
        raise ValidationError('请先关闭评分窗口，再复核并生成名单。')
    if not reason.strip():
        raise ValidationError('请填写本次名单依据；更正时说明原因。')
    rows = round_results(current)
    if not rows or any(not row['complete'] for row in rows):
        raise ValidationError('本轮仍有缺评，不能生成名单。停用或移除账号不会消除已冻结的缺评。')
    if kind == 'finalists':
        tracks = {row['track_id'] for row in rows}
        if (
            not seats
            or set(seats) != tracks
            or any(type(value) is not int or value < 1 for value in seats.values())
            or type(alternates) is not int
            or alternates < 0
        ):
            raise ValidationError('请为本轮每条赛道明确入围名额与候补人数。')
        selections = selections or {}
        unknown = set(selections) - tracks
        if unknown:
            raise ValidationError('人工复核包含本轮以外的赛道。')
        position = 0
        for track in sorted(tracks):
            candidates = [row for row in rows if row['track_id'] == track]
            count = min(seats[track], len(candidates))
            order = selections.get(track)
            if order:
                if len(order) != len(candidates) or set(order) != {row['project_id'] for row in candidates}:
                    raise ValidationError('人工复核顺序必须完整列出该赛道每个作品且不重复。')
                candidates.sort(key=lambda row: order.index(row['project_id']))
            elif count < len(candidates) and candidates[count - 1]['exact'] == candidates[count]['exact']:
                raise ValidationError('入围边界同分，请填写人工复核顺序和依据后生成；系统不会按编号擅自决定。')
            for index, row in enumerate(candidates):
                row['status'] = (
                    'finalist' if index < count else 'alternate' if index < count + alternates else 'excluded'
                )
                position += 1
                row['position'] = position
    else:
        awards = awards or {}
        if set(awards) - {row['project_id'] for row in rows}:
            raise ValidationError('奖项包含本轮以外的作品。')
        for index, row in enumerate(rows, 1):
            row.update(status='award', award=awards.get(row['project_id'], ''), position=index)
    version = (ResultSnapshot.objects.filter(hackathon=h, kind=kind).aggregate(v=Max('version'))['v'] or 0) + 1
    previous = current_signed_snapshot(h, kind)
    from eventyay.hackathon.models import SubmissionSnapshot

    material_snapshot = SubmissionSnapshot.objects.filter(
        hackathon=h, pk=current.rules.get('material_snapshot_id')
    ).first()
    if not material_snapshot or material_snapshot.sha256 != current.rules.get('material_snapshot_sha256'):
        raise ValidationError('本轮绑定的资格快照缺失或校验不符。')
    material_projects = {row['project_id']: row for row in material_snapshot.manifest['entries']}
    for row in rows:
        material_row = material_projects.get(row['project_id'])
        if material_row is None:
            raise ValidationError('材料快照缺少本轮作品，请生成补充材料版本并复核。')
        row['final_material_kind'] = material_row.get('final_material_kind', '')
        row['assets'] = material_row['assets']
    manifest = {
        'material_snapshot_id': str(material_snapshot.pk),
        'material_snapshot_sha256': material_snapshot.sha256,
        'schema': 1,
        'round_id': str(current.pk),
        'round_version': current.version,
        'rules': current.rules,
        'panel': current.panel,
        'entries': rows,
        'seats': seats or {},
        'alternates': alternates,
        'reason': reason,
    }
    result = ResultSnapshot.objects.create(
        hackathon=h,
        round=current,
        kind=kind,
        version=version,
        manifest=manifest,
        sha256=digest(manifest),
        reason=reason,
        supersedes=previous,
    )
    services.audit(h, user, '生成待签认名单', str(result.pk), f'{kind} v{version} {result.sha256}')
    return result


@transaction.atomic
def sign_snapshot(hackathon, user, snapshot, expected_hash):
    h = services.lock_event(hackathon)
    current = ResultSnapshot.objects.select_for_update().select_related('round').get(hackathon=h, pk=snapshot.pk)
    if user.pk != current.round.signer_id or not user.is_active or user.is_spam or not current.round.signer_nonstudent:
        raise PermissionDenied
    if current.signed_at:
        if current.sha256 == expected_hash:
            return current
        raise ValidationError('签认内容不一致，请刷新核对。')
    latest = ResultSnapshot.objects.filter(hackathon=h, kind=current.kind).order_by('-version').first()
    if latest.pk != current.pk or current.sha256 != expected_hash or digest(current.manifest) != expected_hash:
        raise ValidationError('名单已更新或校验不符，请重新核对最新版本。')
    latest_round = JudgingRound.objects.filter(hackathon=h, kind=current.round.kind).order_by('-version').first()
    if latest_round.pk != current.round_id or (
        current.round.kind == 'final'
        and current.round.source_snapshot_id != getattr(current_signed_snapshot(h), 'pk', None)
    ):
        raise ValidationError('轮次或入围名单已有新版本，旧版不能再签认。')
    if current.round.is_open:
        raise ValidationError('请先关闭本轮评分。')
    if current.supersedes_id != getattr(current_signed_snapshot(h, current.kind), 'pk', None):
        raise ValidationError('已生效名单发生变化，请重新生成更正版。')
    fresh = {row['project_id']: row for row in round_results(current.round)}
    if any(
        row['project_id'] not in fresh or row['scores'] != fresh[row['project_id']]['scores']
        for row in current.manifest['entries']
    ):
        raise ValidationError('生成名单后评分发生变化，请重新生成并复核。')
    current.signed_by, current.signed_at = user, timezone.now()
    current.save(update_fields=['signed_by', 'signed_at', 'updated'])
    if current.kind == 'awards':
        for row in current.manifest['entries']:
            Project.objects.filter(hackathon=h, pk=row['project_id']).update(award=row['award'])
    else:
        for row in signed_entries(current):
            previous = (
                FinalistContact.objects.filter(
                    hackathon=h, snapshot_id=current.supersedes_id, team_id=row['team_id']
                ).first()
                if current.supersedes_id
                else None
            )
            defaults = (
                {
                    field: getattr(previous, field)
                    for field in (
                        'channel',
                        'contact',
                        'sent_at',
                        'deadline',
                        'state',
                        'arrived',
                        'equipment_ready',
                        'note',
                    )
                }
                if previous
                else {}
            )
            FinalistContact.objects.get_or_create(
                hackathon=h, snapshot=current, team_id=row['team_id'], defaults=defaults
            )
    services.audit(h, user, '签认名单', str(current.pk), f'{current.kind} v{current.version} {current.sha256}')
    return current


@transaction.atomic
def apply_finalist_queue(hackathon, user, snapshot):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    active = current_signed_snapshot(h)
    if not active or active.pk != snapshot.pk:
        raise ValidationError('只能用当前生效的已签认入围名单生成队列。')
    if h.results_published:
        raise ValidationError('成绩已公布，队列已锁定。')
    rows = sorted(signed_entries(active), key=lambda row: row['position'])
    wanted = [row['project_id'] for row in rows]
    slots = list(DemoSlot.objects.filter(hackathon=h).order_by('position', 'pk'))
    if [str(slot.project_id) for slot in slots] == wanted:
        return slots
    if any(slot.state in {'live', 'done'} for slot in slots):
        raise ValidationError('队列已有上场或完成记录，请先人工处理调度；不会覆盖历史。')
    if Project.objects.filter(hackathon=h, pk__in=wanted, state='approved').count() != len(wanted):
        raise ValidationError('入围作品材料状态已变化，请先处理异常。')
    DemoSlot.objects.filter(hackathon=h).exclude(project_id__in=wanted).delete()
    for position, row in enumerate(rows, 1):
        DemoSlot.objects.update_or_create(hackathon=h, project_id=row['project_id'], defaults={'position': position})
    services.audit(h, user, '按签认名单生成队列', str(active.pk), active.sha256)
    return list(DemoSlot.objects.filter(hackathon=h).order_by('position'))


@transaction.atomic
def update_contact(
    hackathon,
    user,
    contact,
    *,
    revision,
    state,
    channel='',
    contact_text='',
    sent_at=None,
    deadline=None,
    arrived=False,
    equipment_ready=False,
    note='',
):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    current = FinalistContact.objects.select_for_update().get(hackathon=h, pk=contact.pk)
    if current.snapshot_id != getattr(current_signed_snapshot(h), 'pk', None):
        raise ValidationError('这是旧版名单，不能再更改联络结果。')
    if revision != current.revision:
        raise ValidationError('联络台账已更新，请刷新核对。')
    if state not in {'pending', 'sent', 'accepted', 'declined', 'expired'}:
        raise ValidationError('未知联络状态。')
    if state == 'expired' and (not current.deadline or current.deadline > timezone.now()):
        raise ValidationError('尚未到已登记的确认期限。')
    if state == 'accepted' and (
        current.state in {'declined', 'expired'} or (current.deadline and current.deadline <= timezone.now())
    ):
        raise ValidationError('原确认窗口已结束；请先明确重新联络的期限并重新送达。')
    if state == 'accepted' and current.state not in {'sent', 'accepted'}:
        raise ValidationError('请先记录送达与确认窗口，再回填队长确认。')
    if state in {'accepted', 'declined'} and not note.strip():
        raise ValidationError('人工回填确认或拒绝时，请说明实际回复渠道与内容。')
    if state == 'sent':
        if not channel.strip() or not sent_at:
            raise ValidationError('送达须记录联络渠道与实际送达时间。')
        minutes = (
            5
            if current.snapshot.manifest.get('promotion', {}).get('incoming')
            in {row['project_id'] for row in signed_entries(current.snapshot) if row['team_id'] == str(current.team_id)}
            else 15
        )
        deadline = sent_at + timedelta(minutes=minutes)
    current.state, current.channel, current.contact = state, channel, contact_text
    current.sent_at, current.deadline = sent_at, deadline
    current.arrived, current.equipment_ready, current.note = arrived, equipment_ready, note
    current.revision += 1
    current.save()
    services.audit(h, user, '更新入围联络台账', str(current.team_id), f'{current.snapshot_id} {state}；{note}')
    return current


@transaction.atomic
def captain_confirm(hackathon, user, snapshot):
    h = services.lock_event(hackathon)
    if snapshot.pk != getattr(current_signed_snapshot(h), 'pk', None):
        raise ValidationError('此名单版本已失效，请打开当前入围通知。')
    participant = Participant.objects.filter(hackathon=h, user=user).first()
    if not participant or not participant.team_id or participant.team.captain_id != participant.pk:
        raise PermissionDenied
    contact = (
        FinalistContact.objects.select_for_update()
        .filter(hackathon=h, snapshot=snapshot, team_id=participant.team_id)
        .first()
    )
    if not contact:
        raise PermissionDenied
    if contact.state == 'accepted':
        return contact
    if contact.state != 'sent' or not contact.deadline or timezone.now() >= contact.deadline:
        raise ValidationError('确认窗口未开放或已结束，请联系主办方；不会占用新名单席位。')
    contact.state = 'accepted'
    contact.revision += 1
    contact.save(update_fields=['state', 'revision', 'updated'])
    services.audit(h, user, '队长确认入围', str(contact.team_id), str(snapshot.pk))
    return contact


@transaction.atomic
def promote_alternate(hackathon, user, snapshot, outgoing_id, incoming_id, reason):
    require_organizer(hackathon, user)
    h = services.lock_event(hackathon)
    active = current_signed_snapshot(h)
    if h.results_published:
        raise ValidationError('成绩已公布，请先撤销公开并核对更正范围，再生成新的递补与决赛版本。')
    if not active or active.pk != snapshot.pk or not reason.strip():
        raise ValidationError('请在当前生效名单上填写递补理由。')
    outgoing = next((row for row in signed_entries(active) if row['project_id'] == str(outgoing_id)), None)
    incoming = next((row for row in signed_entries(active, 'alternate') if row['project_id'] == str(incoming_id)), None)
    if not outgoing or not incoming or outgoing['track_id'] != incoming['track_id']:
        raise ValidationError('只能在同一赛道用候补替换已入围队伍。')
    contact = FinalistContact.objects.filter(hackathon=h, snapshot=active, team_id=outgoing['team_id']).first()
    if not contact or (
        contact.state not in {'declined', 'expired'}
        and not (contact.deadline and contact.deadline <= timezone.now() and contact.state != 'accepted')
    ):
        raise ValidationError('请先记录原队伍拒绝或确认超时，已确认队伍不能自动移除。')
    if (
        JudgingRound.objects.filter(hackathon=h, kind='final', is_open=True).exists()
        or DemoSlot.objects.filter(hackathon=h, state__in=['live', 'done']).exists()
    ):
        raise ValidationError('决赛评分窗口开放或已有上场记录，请先关闭评分并人工处理调度，避免静默改变应评集合。')
    manifest = json.loads(json.dumps(active.manifest))
    for row in manifest['entries']:
        if row['project_id'] == str(outgoing_id):
            row['status'] = 'withdrawn'
        elif row['project_id'] == str(incoming_id):
            row['status'], row['position'] = 'finalist', outgoing['position']
    manifest['reason'] = reason
    manifest['promotion'] = {'outgoing': str(outgoing_id), 'incoming': str(incoming_id)}
    version = (ResultSnapshot.objects.filter(hackathon=h, kind='finalists').aggregate(v=Max('version'))['v'] or 0) + 1
    result = ResultSnapshot.objects.create(
        hackathon=h,
        round=active.round,
        kind='finalists',
        version=version,
        manifest=manifest,
        sha256=digest(manifest),
        reason=reason,
        supersedes=active,
    )
    services.audit(h, user, '生成待签认递补名单', str(result.pk), reason)
    return result


def can_judge_material(hackathon, user, project_id, kind=None, asset=None):
    """Legacy endpoints may expose only the immutable, assigned round material."""
    rounds = list(JudgingRound.objects.filter(hackathon=hackathon))
    if not rounds:
        return True
    for round in rounds:
        if not can_read_round_material(round, user, project_id):
            continue
        material = round.materials.get(str(project_id), {})
        allowed_kind = 'preliminary' if round.kind == 'preliminary' else material.get('final_material_kind')
        if kind is not None and kind != allowed_kind:
            continue
        if asset is not None and not any(
            row['kind'] == allowed_kind
            and row['id'] == str(asset.pk)
            and row['storage_name'] == asset.file.name
            and row.get('sha256', row.get('checksum')) == asset.checksum
            for row in material.get('assets', [])
        ):
            continue
        return True
    return False


def workflow_results(hackathon):
    """Return the signed awards unchanged; never mix preliminary and final scores."""
    signed = current_signed_snapshot(hackathon, 'awards')
    round = (
        signed.round
        if signed
        else JudgingRound.objects.filter(hackathon=hackathon, kind='final').order_by('-version').first()
    )
    if not round:
        return []
    entries = signed.manifest['entries'] if signed else round_results(round)
    projects = {
        str(p.pk): p
        for p in Project.objects.filter(
            hackathon=hackathon, pk__in=[row['project_id'] for row in entries]
        ).select_related('team__track')
    }
    result = []
    for entry in entries:
        project = projects.get(entry['project_id'])
        if project is None:
            continue
        project.title = entry['title']
        project.team.name = entry['team']
        project.award = entry.get('award', '') if signed else project.award
        trim = (
            round.rules['trim']
            if round.rules['aggregation'] == 'trimmed' and entry['confirmed'] > 2 * round.rules['trim']
            else 0
        )
        result.append(
            {
                'project': project,
                'average': Decimal(entry['average']) if entry['average'] is not None else None,
                'rank': entry['rank'],
                'count': entry['confirmed'],
                'expected': entry['expected'],
                'complete': entry['complete'],
                'counted': entry['confirmed'] - 2 * trim,
                'trimmed': 2 * trim,
                'thin_panel': False,
                'breakthrough': 0,
                'creativity': 0,
                'snapshot': signed,
            }
        )
    return result


def workflow_progress(hackathon):
    round = (
        JudgingRound.objects.filter(hackathon=hackathon, kind='final').order_by('-version').first()
        or JudgingRound.objects.filter(hackathon=hackathon, kind='preliminary').order_by('-version').first()
    )
    if not round:
        return []
    from eventyay.base.models import User

    users = {str(user.pk): user for user in User.objects.filter(pk__in=[row['user_id'] for row in round.panel])}
    projects = {
        str(p.pk): p
        for p in Project.objects.filter(hackathon=hackathon, pk__in=[task['project_id'] for task in round.tasks])
    }
    scores = {
        (str(s.judge_id), str(s.project_id)): s for s in RoundScore.objects.filter(hackathon=hackathon, round=round)
    }
    rows = []
    for member in round.panel:
        tasks = [task for task in round.tasks if member['user_id'] in task['judges']]
        outstanding, confirmed, drafts = [], 0, 0
        for task in tasks:
            score = scores.get((member['user_id'], task['project_id']))
            if score and score.final:
                confirmed += 1
            else:
                drafts += bool(score)
                outstanding.append({'project': projects[task['project_id']], 'draft': bool(score)})
        rows.append(
            {
                'user': users[member['user_id']],
                'expected': len(tasks),
                'confirmed': confirmed,
                'drafts': drafts,
                'outstanding': outstanding,
                'complete': bool(tasks) and not outstanding,
            }
        )
    return rows


def can_read_round_material(round, user, project_id):
    if not user.is_authenticated or not user.is_active or user.is_spam:
        return False
    material = round.materials.get(str(project_id))
    if not material:
        return False
    if Participant.objects.filter(hackathon=round.hackathon, user=user, team_id=material['team_id']).exists():
        return False
    if user.pk == round.signer_id:
        return True
    if str(user.pk) in task_judges(round, project_id):
        return StaffAssignment.objects.filter(hackathon=round.hackathon, role='judge', user=user).exists()
    return any(
        row['user_id'] == str(user.pk) and (not row['tracks'] or material['track_id'] in row['tracks'])
        for row in round.rules.get('reviewers', [])
    )


def station_materials(hackathon):
    """The station consumes selected final-round source objects, never live replacements."""
    if not JudgingRound.objects.filter(hackathon=hackathon).exists():
        return None
    finalists = current_signed_snapshot(hackathon)
    final = JudgingRound.objects.filter(hackathon=hackathon, kind='final').order_by('-version').first()
    if not finalists or not final or final.source_snapshot_id != finalists.pk:
        raise ValidationError('请先冻结当前入围名单对应的决赛材料，再生成固定路演包。')
    entries = sorted(signed_entries(finalists), key=lambda row: row['position'])
    allowed = {row['project_id'] for row in entries}
    if {task['project_id'] for task in final.tasks} != allowed:
        raise ValidationError('决赛任务与当前入围名单不一致，请重新冻结决赛材料。')
    result = []
    for entry in entries:
        project_id = entry['project_id']
        material = final.materials.get(project_id, {})
        kind = material.get('final_material_kind')
        source = next((row for row in material.get('assets', []) if row['kind'] == kind), None)
        if (
            kind not in {'slides', 'video'}
            or not source
            or not source.get('storage_name')
            or not source.get('sha256', source.get('checksum'))
        ):
            raise ValidationError('决赛上场材料缺失，请先冻结完整材料并生成固定路演包。')
        result.append(dict(source, project_id=project_id, round_id=str(final.pk)))
    return result


def active_rule_description(hackathon):
    """Use the rule version that produced these results, including signed history."""
    snapshot = current_signed_snapshot(hackathon, 'awards')
    round = (
        snapshot.round
        if snapshot
        else JudgingRound.objects.filter(hackathon=hackathon, kind='final').order_by('-version').first()
    )
    if not round:
        from eventyay.hackathon.scoring import rule_description

        return rule_description(hackathon)
    rules = snapshot.manifest['rules'] if snapshot else round.rules
    criteria = ' · '.join(f'{row["label"]} {row["weight"]}%' for row in rules['criteria'])
    method = (
        f'各去掉{rules["trim"]}个最高与最低总分后平均；份数不足时保留全部有效评分'
        if rules['aggregation'] == 'trimmed' and rules['trim']
        else '全部有效评分取平均'
    )
    return f'{round.name} v{round.version}：{criteria}。{method}；保留两位小数。初评分仅用于入围，不计入决赛。'
