"""Organizer material policy, fixed captures and verifiable offline packages."""

import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from eventyay.hackathon.downloads import stream_file
from eventyay.hackathon.models import JudgingRound, MaterialPolicy, SubmissionSnapshot
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.services import audit, lock_event
from eventyay.hackathon.storage import STORAGE_ERRORS, PrivateStorage
from eventyay.hackathon.submission_materials import capture_snapshot


class PolicyForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = MaterialPolicy
        fields = ['enabled', 'require_preliminary_video', 'preliminary_max_seconds', 'require_final_material']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].initial = self.instance.revision if self.instance.pk else 0
        self.fields['preliminary_max_seconds'].min_value = 1

    def clean(self):
        data = super().clean()
        if data.get('enabled') and data.get('require_preliminary_video') and not data.get('preliminary_max_seconds'):
            self.add_error('preliminary_max_seconds', '启用初评视频要求时，请填写已确认的时长上限。')
        return data


@event_view('organizer')
def materials(request):
    from eventyay.hackathon.views import page

    h = request.hackathon
    policy = MaterialPolicy.objects.filter(hackathon=h).first()
    form = PolicyForm(
        request.POST if request.method == 'POST' and request.POST.get('action') == 'policy' else None, instance=policy
    )
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'capture':
                capture_snapshot(h, request.user, request.POST.get('reason', ''))
                messages.success(request, '已生成固定资格材料快照；后续续传和修改不会改变本版。')
                return redirect(request.path)
            if request.POST.get('action') == 'policy' and form.is_valid():
                with transaction.atomic():
                    lock_event(h)
                    current = MaterialPolicy.objects.filter(hackathon=h).first()
                    if form.cleaned_data['version'] != (current.revision if current else 0):
                        raise ValidationError('材料规则已更新，请刷新后重新核对。')
                    saved = form.save(commit=False)
                    saved.hackathon = h
                    saved.revision = (current.revision if current else 0) + 1
                    saved.save()
                    audit(h, request.user, '更新材料规则', f'V{saved.revision}')
                messages.success(request, '材料规则已保存。上传凭证的原续传期限继续有效。')
                return redirect(request.path)
        except ValidationError as error:
            form.add_error(None, error)
    return page(request, 'material_policy', form=form, snapshots=SubmissionSnapshot.objects.filter(hackathon=h))


@event_view('organizer')
@require_GET
def snapshot(request, snapshot_id):
    item = get_object_or_404(SubmissionSnapshot, pk=snapshot_id, hackathon=request.hackathon)
    response = JsonResponse(
        {'id': item.pk, 'sha256': item.sha256, 'manifest': item.manifest}, json_dumps_params={'ensure_ascii': False}
    )
    response['Content-Disposition'] = f'attachment; filename="material-snapshot-v{item.version}.json"'
    return response


def safe_name(value):
    return re.sub(r'[^\w.\-\u4e00-\u9fff]+', '_', str(value))[:80].strip('._') or 'team'


def frozen_bundle(request):
    """Returns None only for legacy events without a signed workflow or material policy."""
    from eventyay.hackathon.workflow_services import current_signed_snapshot

    h = request.hackathon
    signed = current_signed_snapshot(h, 'finalists')
    policy = MaterialPolicy.objects.filter(hackathon=h, enabled=True).first()
    if signed is None:
        if policy:
            return HttpResponse(
                '请先生成并签认入围名单，再下载固定路演包。原始材料归档可用于检查，不能作为签认上场包。', status=409
            )
        return None
    final_round = JudgingRound.objects.filter(hackathon=h, kind='final').order_by('-version').first()
    if final_round is None or final_round.source_snapshot_id != signed.pk:
        return HttpResponse('请先按当前签认入围名单冻结决赛轮次与材料，再下载路演包。', status=409)
    material_id = final_round.rules.get('material_snapshot_id')
    material_sha = final_round.rules.get('material_snapshot_sha256')
    frozen = (
        SubmissionSnapshot.objects.filter(pk=material_id, hackathon=h, sha256=material_sha).first()
        if material_id
        else None
    )
    if frozen is None:
        return HttpResponse(
            '这版入围名单尚未绑定固定材料快照，请生成并重新签认新版本；未下载可漂移的材料包。', status=409
        )
    by_id = {row['project_id']: row for row in frozen.manifest.get('entries', [])}
    selected = sorted(
        (row for row in signed.manifest.get('entries', []) if row.get('status') == 'finalist'),
        key=lambda row: row.get('position', row.get('rank', 0)),
    )
    issues, files = [], []
    for index, row in enumerate(selected, 1):
        item = by_id.get(str(row['project_id']))
        if not item:
            issues.append(f'{row.get("team", row["project_id"])}：快照缺少作品')
            continue
        chosen = item.get('final_material_kind')
        asset = next((a for a in item.get('assets', []) if a['kind'] == chosen), None)
        if (
            chosen not in ('slides', 'video')
            or not asset
            or (chosen == 'slides' and not asset['filename'].lower().endswith('.pdf'))
        ):
            issues.append(f'{item["team"]}：没有选定有效的上场 PDF 或视频')
            continue
        filename = (
            f'{index:03d}_{safe_name(item["team_code"])}_{safe_name(item["team"])}_'
            f'{safe_name(item.get("captain", ""))}{Path(asset["filename"]).suffix.lower()}'
        )
        files.append((filename, asset, item))
    if not selected:
        issues.append('签认名单中没有入围队伍')
    if issues:
        return JsonResponse(
            {'error': '材料包未生成，请补齐后生成并签认新版本。', 'issues': issues},
            status=409,
            json_dumps_params={'ensure_ascii': False},
        )
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    manifest = {
        'result_snapshot_id': str(signed.pk),
        'final_round_id': str(final_round.pk),
        'final_round_version': final_round.version,
        'result_snapshot_sha256': signed.sha256,
        'material_snapshot_id': frozen.pk,
        'material_snapshot_sha256': frozen.sha256,
        'files': [],
    }
    try:
        storage = PrivateStorage()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for filename, asset, item in files:
                digest, count = hashlib.sha256(), 0
                try:
                    with (
                        storage.open(asset['storage_name'], 'rb') as source,
                        archive.open(filename, 'w') as destination,
                    ):
                        while block := source.read(1024 * 1024):
                            count += len(block)
                            digest.update(block)
                            destination.write(block)
                except STORAGE_ERRORS:
                    issues.append(f'{item["team"]}：固定材料无法读取，请联系技术人员')
                    continue
                if count != asset['size'] or digest.hexdigest() != asset['sha256']:
                    issues.append(f'{item["team"]}：固定材料校验不一致')
                manifest['files'].append(
                    {
                        'filename': filename,
                        'team_code': item['team_code'],
                        'captain': item.get('captain', ''),
                        'receipt': item['receipt'],
                        'size': count,
                        'sha256': digest.hexdigest(),
                    }
                )
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr(
                'README.txt',
                '文件顺序与本版已签认入围名单一致。请在现场设备离线测试翻页、画面、声音；不要运行未知脚本。',
            )
        if issues:
            output.close()
            return JsonResponse(
                {'error': '材料包未交付，存在缺件或校验失败。', 'issues': issues},
                status=409,
                json_dumps_params={'ensure_ascii': False},
            )
        size = output.tell()
        output.seek(0)
        audit(h, request.user, '下载固定路演材料包', f'名单V{signed.version} / 材料V{frozen.version}')
        return stream_file(request, output, size, f'{h.slug}-finals-v{signed.version}-materials-v{frozen.version}.zip')
    except STORAGE_ERRORS:
        output.close()
        return HttpResponse('固定材料存储暂不可用，请稍后重试。', status=503)
