from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path
from django.views.decorators.http import require_GET, require_http_methods, require_safe

from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.downloads import asset_response
from eventyay.hackathon.models import Asset, Project, StaffAssignment, Track
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.staff_tools import private_media_source
from eventyay.hackathon.storage import STORAGE_ERRORS
from eventyay.hackathon.views import page, url
from eventyay.hackathon.workflow_forms import ROUND_CRITERIA, ContactForm, RoundForm, RoundScoreForm, SnapshotForm
from eventyay.hackathon.workflow_models import FinalistContact, JudgingRound, ResultSnapshot, RoundScore


def address(h, name, **kwargs):
    return url(h, 'workflow-' + name, **kwargs)


def round_entry(h, id):
    return get_object_or_404(JudgingRound, hackathon=h, pk=id)


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def manage(request):
    h = request.hackathon
    form = RoundForm(request.POST or None, hackathon=h, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            result = workflow.create_round(
                h,
                request.user,
                kind=form.cleaned_data['kind'],
                name=form.cleaned_data['name'],
                rules=form.rules(),
                panel=form.cleaned_data['panel'],
                signer=form.cleaned_data['signer'],
                signer_nonstudent=form.cleaned_data['signer_nonstudent'],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, '本轮规则、评委分工和材料快照已冻结；核对后可开放评分。')
            return redirect(address(h, 'round', round_id=result.pk))
    return page(
        request,
        'workflow_manage',
        form=form,
        rounds=JudgingRound.objects.filter(hackathon=h),
        snapshots=ResultSnapshot.objects.filter(hackathon=h).select_related('signed_by', 'round'),
        tracks=Track.objects.filter(hackathon=h),
        judges=StaffAssignment.objects.filter(hackathon=h, role='judge').select_related('user'),
        documented_criteria=ROUND_CRITERIA,
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def round_manage(request, round_id):
    h = request.hackathon
    item = round_entry(h, round_id)
    form = SnapshotForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'snapshot' else None)
    if item.kind == 'final':
        for key in ('seats', 'alternates', 'selections'):
            form.fields[key].required = False
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action in {'open', 'close'}:
                workflow.set_round_open(h, request.user, item, action == 'open')
                messages.success(request, '本轮评分窗口已开放。' if action == 'open' else '本轮评分窗口已关闭。')
                return redirect(address(h, 'round', round_id=item.pk))
            if action == 'snapshot' and form.is_valid():
                rows = workflow.round_results(item)
                seats = (
                    {row['track_id']: form.cleaned_data['seats'] for row in rows}
                    if item.kind == 'preliminary'
                    else None
                )
                result = workflow.make_snapshot(
                    h,
                    request.user,
                    item,
                    kind='finalists' if item.kind == 'preliminary' else 'awards',
                    reason=form.cleaned_data['reason'],
                    seats=seats,
                    alternates=form.cleaned_data.get('alternates') or 0,
                    selections=form.cleaned_data.get('selections'),
                    awards=form.cleaned_data.get('awards'),
                )
                return redirect(address(h, 'snapshot', snapshot_id=result.pk))
            if action not in {'open', 'close', 'snapshot'}:
                raise ValidationError('未知操作，本次没有更改轮次。')
        except ValidationError as error:
            form.add_error(None, error)
    return page(request, 'workflow_round', round=item, rows=workflow.round_results(item), form=form)


@event_view('login')
@require_http_methods(['GET', 'POST'])
def snapshot(request, snapshot_id):
    h = request.hackathon
    item = get_object_or_404(ResultSnapshot.objects.select_related('round', 'signed_by'), hackathon=h, pk=snapshot_id)
    if not request.hackathon_organizer and request.user.pk != item.round.signer_id:
        raise PermissionDenied
    errors = []
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'sign':
                workflow.sign_snapshot(h, request.user, item, request.POST.get('sha256', ''))
                messages.success(request, '本版本名单已签认，内容和来源评分已锁定。')
            elif action == 'queue':
                workflow.apply_finalist_queue(h, request.user, item)
                messages.success(request, '路演队列已与本版入围名单一致。')
            elif action == 'promote':
                result = workflow.promote_alternate(
                    h,
                    request.user,
                    item,
                    request.POST.get('outgoing'),
                    request.POST.get('incoming'),
                    request.POST.get('reason', ''),
                )
                return redirect(address(h, 'snapshot', snapshot_id=result.pk))
            else:
                raise ValidationError('未知名单操作。')
        except ValidationError as error:
            errors = error.messages
        else:
            return redirect(address(h, 'snapshot', snapshot_id=item.pk))
    contacts = FinalistContact.objects.filter(hackathon=h, snapshot=item).select_related('team')
    return page(
        request,
        'workflow_snapshot',
        workspace_identity='organizer' if request.hackathon_organizer else 'reviewer',
        snapshot=item,
        errors=errors,
        contacts=contacts,
        can_sign=request.user.pk == item.round.signer_id,
        confirmation_url=address(h, 'confirm', snapshot_id=item.pk),
    )


@event_view('login')
@require_GET
def snapshot_export(request, snapshot_id):
    item = get_object_or_404(
        ResultSnapshot.objects.select_related('round'), hackathon=request.hackathon, pk=snapshot_id
    )
    if not request.hackathon_organizer and request.user.pk != item.round.signer_id:
        raise PermissionDenied
    response = JsonResponse(
        {
            'id': str(item.pk),
            'kind': item.kind,
            'version': item.version,
            'sha256': item.sha256,
            'signed_at': item.signed_at.isoformat() if item.signed_at else None,
            'signed_by': str(item.signed_by_id) if item.signed_by_id else None,
            'manifest': item.manifest,
        },
        json_dumps_params={'ensure_ascii': False, 'indent': 2},
    )
    response['Content-Disposition'] = f'attachment; filename="{item.kind}-v{item.version}.json"'
    return response


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def contact(request, contact_id):
    item = get_object_or_404(
        FinalistContact.objects.select_related('team', 'snapshot'), hackathon=request.hackathon, pk=contact_id
    )
    initial = {
        field: getattr(item, field)
        for field in ('revision', 'state', 'channel', 'sent_at', 'deadline', 'arrived', 'equipment_ready', 'note')
    }
    initial['contact_text'] = item.contact
    form = ContactForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            workflow.update_contact(request.hackathon, request.user, item, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, '本版名单的联络记录已保存。系统没有发送消息。')
            return redirect(address(request.hackathon, 'snapshot', snapshot_id=item.snapshot_id))
    return page(request, 'workflow_contact', contact=item, form=form)


@event_view('login')
@require_http_methods(['GET', 'POST'])
def confirm(request, snapshot_id):
    h = request.hackathon
    item = get_object_or_404(ResultSnapshot, hackathon=h, pk=snapshot_id, signed_at__isnull=False, kind='finalists')
    person = request.participant
    if not person or not person.team_id or person.team.captain_id != person.pk:
        raise PermissionDenied
    contact = get_object_or_404(FinalistContact, hackathon=h, snapshot=item, team_id=person.team_id)
    error = None
    if request.method == 'POST':
        try:
            workflow.captain_confirm(h, request.user, item)
        except ValidationError as issue:
            error = '；'.join(issue.messages)
        else:
            messages.success(request, '已确认参加决赛。')
            if request.POST.get('return') == 'home':
                return redirect(url(h, 'home'))
            return redirect(address(h, 'confirm', snapshot_id=item.pk))
    return page(request, 'workflow_confirm', snapshot=item, contact=contact, error=error)


def score_values(round, values):
    return [
        {
            'label': criterion['label'],
            'weight': criterion['weight'],
            'max': workflow.criterion_max(round.rules, criterion),
            'value': values.get(criterion['key']),
        }
        for criterion in round.rules['criteria']
    ]


def review_entry(round, row):
    names = {member['user_id']: member.get('name', '') for member in round.panel}
    return {
        **row,
        'judge_scores': [
            {
                'judge': names.get(source['judge_id']) or f'评委 {source["judge_id"]}',
                'revision': source['revision'],
                'values': score_values(round, source['values']),
            }
            for source in row['scores']
        ],
    }


def judge_queue(item, user):
    """Works this user may open in this round, in task order."""
    return [
        task['project_id'] for task in item.tasks if workflow.can_read_round_material(item, user, task['project_id'])
    ]


def next_unscored(item, user, current):
    done = {
        str(project_id)
        for project_id in RoundScore.objects.filter(round=item, judge=user, final=True).values_list(
            'project_id', flat=True
        )
    }
    mine = [task['project_id'] for task in item.tasks if str(user.pk) in task['judges']]
    start = mine.index(current) + 1 if current in mine else 0
    return next((pid for pid in mine[start:] + mine[:start] if pid not in done and pid != current), None)


@event_view('login')
@require_GET
def judging(request):
    h = request.hackathon
    rounds = []
    for item in JudgingRound.objects.filter(hackathon=h):
        readable = [
            task for task in item.tasks if workflow.can_read_round_material(item, request.user, task['project_id'])
        ]
        if not readable:
            continue
        item.viewer_can_score = any(str(request.user.pk) in task['judges'] for task in readable)
        item.viewer_can_review = any(str(request.user.pk) not in task['judges'] for task in readable)
        item.viewer_window_open = item.is_open and not h.results_published
        rounds.append(item)
    identity = (
        'judge'
        if any(item.viewer_can_score for item in rounds)
        else 'reviewer'
        if rounds
        else 'judge'
        if 'judge' in request.hackathon_roles
        else None
    )
    from eventyay.hackathon.competition_review import pending

    to_sign = [
        found
        for item in JudgingRound.objects.filter(hackathon=h, signer=request.user)
        if (found := pending(item, 'finalists' if item.kind == 'preliminary' else 'awards'))
    ]
    return page(
        request,
        'workflow_judging',
        rounds=rounds,
        to_sign=to_sign,
        workspace_identity=identity or ('reviewer' if to_sign else None),
    )


@event_view('login')
@require_GET
def tasks(request, round_id):
    item = round_entry(request.hackathon, round_id)
    permitted = {
        task['project_id']
        for task in item.tasks
        if workflow.can_read_round_material(item, request.user, task['project_id'])
    }
    if not permitted:
        raise PermissionDenied
    scores = {
        str(score.project_id): score
        for score in RoundScore.objects.filter(hackathon=request.hackathon, round=item, judge=request.user)
    }
    window_open = item.is_open and not request.hackathon.results_published
    entries = [
        {
            'project_id': project_id,
            'material': material,
            'score': scores.get(project_id),
            'can_score': str(request.user.pk) in workflow.task_judges(item, project_id),
            'can_edit_score': window_open and str(request.user.pk) in workflow.task_judges(item, project_id),
        }
        for project_id, material in item.materials.items()
        if project_id in permitted
    ]
    scoring = [entry for entry in entries if entry['can_score']]
    can_review = request.user.pk == item.signer_id or any(
        row['user_id'] == str(request.user.pk) for row in item.rules.get('reviewers', [])
    )
    return page(
        request,
        'workflow_tasks',
        workspace_identity='judge' if scoring else 'reviewer',
        can_review=can_review,
        review_rows=[review_entry(item, row) for row in workflow.round_results(item) if row['project_id'] in permitted]
        if can_review
        else [],
        snapshots=item.snapshots.all() if request.user.pk == item.signer_id else [],
        round=item,
        entries=entries,
        window_open=window_open,
        total=len(scoring),
        read_total=len(entries),
        confirmed=sum(bool(entry['score'] and entry['score'].final) for entry in scoring),
    )


@event_view('login')
@require_http_methods(['GET', 'POST'])
def score(request, round_id, project_id):
    h = request.hackathon
    item = round_entry(h, round_id)
    if not workflow.can_read_round_material(item, request.user, project_id):
        raise PermissionDenied
    project = get_object_or_404(Project, hackathon=h, pk=project_id)
    existing = RoundScore.objects.filter(hackathon=h, round=item, project=project, judge=request.user).first()
    can_score = str(request.user.pk) in workflow.task_judges(item, project_id)
    window_open = item.is_open and not h.results_published
    can_edit_score = can_score and window_open
    action = request.POST.get('action')
    form = (
        RoundScoreForm(
            request.POST if request.method == 'POST' else None, round=item, existing=existing, confirm=action == 'final'
        )
        if can_edit_score
        else None
    )
    read_only_error = None
    if request.method == 'POST':
        if not can_score:
            raise PermissionDenied
        if not can_edit_score:
            read_only_error = '当前评分窗口已关闭或结果已公布，本次输入未保存；下方显示已保存记录。'
        elif action not in {'draft', 'final'}:
            form.add_error(None, '请选择保存草稿或确认评分。')
        elif form.is_valid():
            try:
                workflow.save_round_score(
                    h,
                    request.user,
                    item,
                    project,
                    values=form.values(),
                    final=action == 'final',
                    revision=form.cleaned_data['revision'],
                    feedback=form.cleaned_data['feedback'],
                )
            except ValidationError as error:
                item.refresh_from_db(fields=['is_open'])
                h.refresh_from_db(fields=['results_published'])
                window_open = item.is_open and not h.results_published
                can_edit_score = can_score and window_open
                if can_edit_score:
                    form.add_error(None, error)
                else:
                    form = None
                    read_only_error = '；'.join(error.messages)
                    existing = RoundScore.objects.filter(
                        hackathon=h, round=item, project=project, judge=request.user
                    ).first()
            else:
                messages.success(request, '评分已确认。' if action == 'final' else '草稿已保存，尚未计分。')
                if action == 'final':
                    following = next_unscored(item, request.user, str(project.pk))
                    if following:
                        return redirect(address(h, 'score', round_id=item.pk, project_id=following))
                return redirect(address(h, 'tasks', round_id=item.pk))
    material = item.materials[str(project.pk)]
    review_row = (
        next((row for row in workflow.round_results(item) if row['project_id'] == str(project_id)), None)
        if not can_score
        else None
    )
    queue = judge_queue(item, request.user)
    here = queue.index(str(project.pk)) if str(project.pk) in queue else None
    # Only immutable IDs from this round are presented; playback endpoints re-check current authorization.
    return page(
        request,
        'workflow_score',
        workspace_identity='judge' if can_score else 'reviewer',
        round=item,
        project=project,
        material=material,
        form=form,
        score=existing,
        saved_values=score_values(item, existing.values) if existing else [],
        review_row=review_entry(item, review_row) if review_row else None,
        can_score=can_score,
        can_edit_score=can_edit_score,
        window_open=window_open,
        read_only_error=read_only_error,
        position=here + 1 if here is not None else None,
        queue_total=len(queue),
        previous_id=queue[here - 1] if here else None,
        next_id=queue[here + 1] if here is not None and here + 1 < len(queue) else None,
        score_fields=[(form[row['key']], row) for row in item.rules['criteria']] if form else [],
    )


def frozen_asset(request, round_id, project_id, kind):
    item = round_entry(request.hackathon, round_id)
    if not workflow.can_read_round_material(item, request.user, project_id):
        raise PermissionDenied
    material = item.materials.get(str(project_id), {})
    allowed = 'preliminary' if item.kind == 'preliminary' else material.get('final_material_kind')
    if kind != allowed:
        raise Http404
    row = next((row for row in material.get('assets', []) if row['kind'] == kind), None)
    if not row:
        raise Http404
    return Asset(
        hackathon=request.hackathon,
        project_id=project_id,
        kind=kind,
        file=row['storage_name'],
        filename=row['filename'],
        size=row['size'],
        checksum=row.get('sha256', row.get('checksum', '')),
    )


@event_view('login')
@require_safe
def media_source(request, round_id, project_id, kind):
    asset = frozen_asset(request, round_id, project_id, kind)
    if kind not in {'preliminary', 'video'}:
        raise Http404
    try:
        data = private_media_source(
            asset.file.storage,
            asset.file.name,
            asset.filename,
            address(request.hackathon, 'media', round_id=round_id, project_id=project_id, kind=kind),
        )
    except FileNotFoundError as error:
        raise Http404('冻结材料文件缺失，请报告异常。') from error
    except STORAGE_ERRORS:
        return JsonResponse({'error': '存储暂时不可用，请稍后重试。'}, status=503)
    response = JsonResponse(data)
    response['Referrer-Policy'] = 'no-referrer'
    return response


@event_view('login')
@require_safe
def media(request, round_id, project_id, kind):
    return asset_response(
        request, frozen_asset(request, round_id, project_id, kind), attachment=kind not in {'preliminary', 'video'}
    )


event_patterns = [
    path(
        'judging/materials/<uuid:round_id>/<uuid:project_id>/<str:kind>/source/',
        media_source,
        name='workflow-media-source',
    ),
    path('judging/materials/<uuid:round_id>/<uuid:project_id>/<str:kind>/', media, name='workflow-media'),
    path('manage/workflow/', manage, name='workflow-manage'),
    path('manage/workflow/rounds/<uuid:round_id>/', round_manage, name='workflow-round'),
    path('workflow/snapshots/<uuid:snapshot_id>/', snapshot, name='workflow-snapshot'),
    path('workflow/snapshots/<uuid:snapshot_id>/export/', snapshot_export, name='workflow-export'),
    path('workflow/snapshots/<uuid:snapshot_id>/confirm/', confirm, name='workflow-confirm'),
    path('manage/workflow/contacts/<int:contact_id>/', contact, name='workflow-contact'),
    path('judging/rounds/', judging, name='workflow-judging'),
    path('judging/rounds/<uuid:round_id>/', tasks, name='workflow-tasks'),
    path('judging/rounds/<uuid:round_id>/<uuid:project_id>/', score, name='workflow-score'),
]
