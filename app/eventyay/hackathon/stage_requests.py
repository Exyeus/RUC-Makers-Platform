"""Participant evidence and organizer stage-order decisions, with private downloads."""

import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from eventyay.hackathon import services
from eventyay.hackathon.calendar import absence_summary
from eventyay.hackathon.downloads import stream_file
from eventyay.hackathon.models import DemoSlot, Participant
from eventyay.hackathon.permissions import event_view, is_organizer
from eventyay.hackathon.stage_models import StageOrderRequest


EVIDENCE_LIMIT = 10 * 1024 * 1024


def url(hackathon, name, **kwargs):
    return reverse(f'hackathon:{name}', kwargs={'hackathon_slug': hackathon.slug, **kwargs})


class RequestForm(forms.ModelForm):
    class Meta:
        model = StageOrderRequest
        fields = ['preference', 'time_constraint', 'reason', 'evidence', 'evidence_url']
        widgets = {'reason': forms.Textarea(attrs={'rows': 4})}
        help_texts = {
            'reason': '请说明为什么不能接受原安排。申请不会自动改变顺序，由主办核查后决定。',
            'evidence': '证明文件或链接至少提供一项。仅本人和主办可见；PDF、JPG、PNG，最多10 MiB。',
            'evidence_url': '请提供主办可核验、不会公开给其他选手的证明链接。',
            'time_constraint': '例如15:30后必须离场；请写清日期和具体时间。',
        }

    def clean_evidence(self):
        upload = self.cleaned_data.get('evidence')
        if not upload:
            return upload
        if not 0 < upload.size <= EVIDENCE_LIMIT:
            raise ValidationError('证明文件须为1字节至10 MiB。')
        suffix = Path(upload.name).suffix.lower()
        if suffix not in {'.pdf', '.png', '.jpg', '.jpeg'}:
            raise ValidationError('证明文件仅支持PDF、JPG或PNG。')
        data = upload.read(EVIDENCE_LIMIT + 1)
        upload.seek(0)
        if len(data) != upload.size:
            raise ValidationError('证明文件大小不一致，请重新选择文件。')
        if suffix == '.pdf':
            if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
                raise ValidationError('文件内容不是完整的PDF，请重新导出。')
        else:
            try:
                with Image.open(io.BytesIO(data)) as image:
                    expected = 'PNG' if suffix == '.png' else 'JPEG'
                    if image.format != expected:
                        raise ValidationError('图片内容与文件类型不一致。')
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
                raise ValidationError('无法读取证明图片，请重新导出。') from error
        self.evidence_digest = hashlib.sha256(data).hexdigest()
        return upload

    def clean(self):
        data = super().clean()
        if not data.get('evidence') and not data.get('evidence_url'):
            raise ValidationError('调整路演顺序须提交证明文件或可核验的证明链接。')
        if data.get('preference') == StageOrderRequest.Preference.TIME and not data.get('time_constraint', '').strip():
            self.add_error('time_constraint', '请说明具体的时间限制。')
        return data


class DecisionForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    queue_version = forms.CharField(max_length=64, widget=forms.HiddenInput)
    decision = forms.ChoiceField(label='处理结果', choices=[('accepted', '同意并调整队列'), ('rejected', '暂不能调整')])
    position = forms.TypedChoiceField(label='调整到候场位置', coerce=int, required=False)
    note = forms.CharField(label='给申请人的说明', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, slots=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['position'].choices = [('', '同意申请时必须选择实际位置')] + [
            (slot.position, f'{slot.position} · {slot.project.team.name}')
            for slot in slots
            if slot.state not in {'live', 'done'}
        ]

    def clean(self):
        data = super().clean()
        if data.get('decision') == 'accepted' and not data.get('position'):
            self.add_error('position', '请选择实际调整后的候场位置。')
        return data


def queue_snapshot(slots):
    return [{'id': str(s.pk), 'position': s.position, 'state': s.state, 'revision': s.project.revision} for s in slots]


def queue_version(slots):
    return hashlib.sha256(json.dumps(queue_snapshot(slots), sort_keys=True).encode()).hexdigest()


def require_request_open(h):
    if h.results_published:
        raise ValidationError('成绩已公布，路演顺序申请已关闭。')


def submit_request(h, p, form):
    """Only the registered participant may assert their own time constraint."""
    evidence_name = None
    storage = None
    try:
        with transaction.atomic():
            h = services.lock_event(h)
            require_request_open(h)
            p = Participant.objects.select_for_update().get(pk=p.pk, hackathon=h)
            if not p.team_id or p.team.state == 'dissolved':
                raise ValidationError('请先加入正式队伍，再提交路演顺序申请。')
            if (
                StageOrderRequest.objects.filter(
                    hackathon=h, participant=p, created__gte=timezone.now() - timedelta(days=1)
                ).count()
                >= 5
            ):
                raise ValidationError('今日已提交5次申请，请稍后重试或联系支持，避免重复上传证明。')
            if StageOrderRequest.objects.filter(hackathon=h, participant=p, state='pending').exists():
                raise ValidationError('已有待处理申请，请先查看或撤回，避免重复安排。')
            if DemoSlot.objects.filter(hackathon=h, project__team=p.team, state__in=['live', 'done']).exists():
                raise ValidationError('本队已开始或完成路演，请通过支持工单联系现场主办。')
            item = form.save(commit=False)
            item.hackathon, item.participant, item.team = h, p, p.team
            upload = form.cleaned_data.get('evidence')
            if upload:
                item.evidence_name = Path(upload.name).name[:190]
                item.evidence_size = upload.size
                item.evidence_checksum = form.evidence_digest
                item.evidence.save(upload.name, upload, save=False)
                evidence_name, storage = item.evidence.name, item.evidence.storage
            item.save()
            services.audit(
                h, p.user, '提交路演顺序申请', item.pk, f'队伍{item.team.code}；{item.preference}；证明已提交'
            )
        return item
    except BaseException:
        # A failed transaction must not leave an unreferenced private proof file.
        if evidence_name:
            storage.delete(evidence_name)
        raise


@transaction.atomic
def decide_request(h, actor, data):
    if not is_organizer(actor, h):
        raise PermissionDenied
    h = services.lock_event(h)
    require_request_open(h)
    if data.get('decision') not in {'accepted', 'rejected'} or not data.get('note', '').strip():
        raise ValidationError('请提供明确处理结果和给选手的说明。')
    item = get_object_or_404(
        StageOrderRequest.objects.select_for_update().select_related('participant', 'team'),
        hackathon=h,
        pk=data['request_id'],
    )
    if item.state != 'pending' or item.version != data['version']:
        raise ValidationError('申请已经处理或撤回，请刷新页面。')
    slots = list(DemoSlot.objects.filter(hackathon=h).select_related('project__team').order_by('position', 'pk'))
    snapshot = queue_snapshot(slots)
    if data['queue_version'] != queue_version(slots):
        raise ValidationError('路演队列已更新，请刷新后核对申请与候场位置。')
    if data['decision'] == 'accepted':
        if not item.evidence and not item.evidence_url:
            raise ValidationError('申请缺少证明，请先补交证明再决定调整。')
        if item.participant.team_id != item.team_id or item.team.state == 'dissolved':
            raise ValidationError('申请人已离开申请时的队伍，不能按旧申请调整；请说明后拒绝或请本人重新申请。')
        slot = next((s for s in slots if s.project.team_id == item.team_id), None)
        if not slot or slot.project.state != 'approved':
            raise ValidationError('该队作品尚未通过检查并加入队列，请先完成材料检查。')
        if slot.state in {'live', 'done'}:
            raise ValidationError('本队已开始或完成路演，不能通过申请调整顺序。')
        adjustable = [s for s in slots if s.state not in {'live', 'done'}]
        positions = [s.position for s in adjustable]
        if data['position'] not in positions:
            raise ValidationError('所选位置不可调整，请刷新后重试。')
        original_position = slot.position
        if item.preference == 'early' and data['position'] > original_position:
            raise ValidationError('申请希望靠前，不能调整到更靠后的位置。')
        if item.preference == 'late' and data['position'] < original_position:
            raise ValidationError('申请希望靠后，不能调整到更靠前的位置。')
        adjustable.remove(slot)
        adjustable.insert(positions.index(data['position']), slot)
        for position, moved in zip(positions, adjustable, strict=True):
            if moved.position != position:
                moved.position = position
                moved.save(update_fields=['position', 'updated'])
        item.applied_position = slot.position
        item.decision_snapshot = {
            'before': snapshot,
            'after': queue_snapshot(sorted(slots, key=lambda s: (s.position, s.pk))),
        }
    item.state = data['decision']
    item.decision_note = data['note'].strip()
    item.decided_by, item.decided_at = actor, timezone.now()
    item.version += 1
    item.save()
    services.audit(h, actor, '处理路演顺序申请', item.pk, f'{item.state}；位置{item.applied_position or "未调整"}')
    return item


def manage_context(h):
    people = Participant.objects.filter(hackathon=h).select_related('team').order_by('team__name', 'name')
    attendance = []
    for p in people:
        summary = absence_summary(h, p.unavailable_slots, p.availability)
        if summary:
            attendance.append({'name': p.name, 'team': p.team.name if p.team else '尚未组队', 'summary': summary})
    return {
        'stage_attendance': attendance,
        'stage_request_pending': StageOrderRequest.objects.filter(hackathon=h, state='pending').count(),
    }


@event_view('login')
@require_http_methods(['GET', 'POST'])
def requests(request):
    # Imported at runtime because the shared page helper includes stage request context.
    from eventyay.hackathon.views import page

    h, p = request.hackathon, request.participant
    if not p:
        return redirect(url(h, 'register'))
    if request.method == 'POST' and request.POST.get('action') == 'withdraw':
        try:
            request_id = forms.UUIDField().clean(request.POST.get('request_id'))
        except ValidationError:
            raise Http404 from None
        with transaction.atomic():
            services.lock_event(h)
            item = get_object_or_404(
                StageOrderRequest.objects.select_for_update(), hackathon=h, participant=p, pk=request_id
            )
            if item.state == 'pending':
                item.state = 'withdrawn'
                item.version += 1
                item.save(update_fields=['state', 'version', 'updated'])
                services.audit(h, request.user, '撤回路演顺序申请', item.pk)
                messages.success(request, '申请已撤回。')
            else:
                messages.warning(request, '申请已处理或撤回，请查看现有回执。')
        return redirect(url(h, 'stage-requests'))
    form = RequestForm(
        request.POST if request.method == 'POST' else None, request.FILES if request.method == 'POST' else None
    )
    if request.method == 'POST':
        try:
            valid = form.is_valid()
            if valid:
                submit_request(h, p, form)
        except ValidationError as error:
            form.add_error(None, error)
        except OSError:
            form.add_error(None, '证明文件暂时无法保存，其他内容已保留。请重新选择文件后重试；持续失败请联系支持。')
        else:
            if valid:
                messages.success(request, '申请与证明已提交。请在本页查看主办的安排回执。')
                return redirect(url(h, 'stage-requests'))
    own = Q(participant=p)
    if p.team_id and p.team.captain_id == p.pk:
        own |= Q(team=p.team)
    items = list(StageOrderRequest.objects.filter(hackathon=h).filter(own).select_related('participant', 'team'))
    for item in items:
        item.private_detail = item.participant_id == p.pk
    own_slot = DemoSlot.objects.filter(hackathon=h, project__team_id=p.team_id).first() if p.team_id else None
    return page(
        request,
        'stage_requests',
        title='路演顺序申请与回执',
        form=form,
        requests=items,
        has_team=bool(p.team_id),
        own_slot=own_slot,
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def manage(request):
    from eventyay.hackathon.views import page

    h = request.hackathon
    slots = list(DemoSlot.objects.filter(hackathon=h).select_related('project__team').order_by('position', 'pk'))
    bound_form = DecisionForm(request.POST, slots=slots) if request.method == 'POST' else None
    if bound_form and bound_form.is_valid():
        try:
            decide_request(h, request.user, bound_form.cleaned_data)
        except ValidationError as error:
            bound_form.add_error(None, error)
        else:
            messages.success(request, '处理结果已保存；同意的申请已实际调整队列，选手可查看回执。')
            return redirect(url(h, 'stage-requests-manage'))
    items = list(StageOrderRequest.objects.filter(hackathon=h).select_related('participant', 'team', 'decided_by'))
    if bound_form and not any(request.POST.get('request_id') == str(item.pk) for item in items):
        messages.error(request, '申请编号无效，请刷新页面后再处理。')
    version = queue_version(slots)
    slot_by_team = {slot.project.team_id: slot for slot in slots}
    for item in items:
        item.current_slot = slot_by_team.get(item.team_id)
        item.decision_form = (
            bound_form
            if bound_form and request.POST.get('request_id') == str(item.pk)
            else DecisionForm(
                initial={'request_id': item.pk, 'version': item.version, 'queue_version': version}, slots=slots
            )
        )
    return page(request, 'stage_request_manage', title='处理路演顺序申请', requests=items, **manage_context(h))


@event_view('login')
@require_http_methods(['GET', 'HEAD'])
def evidence(request, request_id):
    item = get_object_or_404(
        StageOrderRequest.objects.select_related('participant'), hackathon=request.hackathon, pk=request_id
    )
    if not request.hackathon_organizer and item.participant.user_id != request.user.pk:
        raise PermissionDenied
    if not item.evidence:
        raise Http404
    try:
        source = item.evidence.open('rb')
    except OSError as error:
        raise Http404('证明文件暂不可用，请联系主办方。') from error
    response = stream_file(request, source, item.evidence_size, item.evidence_name, checksum=item.evidence_checksum)
    response['Cache-Control'] = 'private, no-store'
    return response
