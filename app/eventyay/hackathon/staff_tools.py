"""Event-scoped account provisioning and authorized private media delivery."""

import csv
import io
import secrets
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_safe

from eventyay.base.models import User
from eventyay.hackathon import services
from eventyay.hackathon.downloads import asset_response
from eventyay.hackathon.models import Asset, StaffAssignment
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.storage import STORAGE_ERRORS, S3Storage
from eventyay.hackathon.views import page, url
from eventyay.hackathon.workflow_services import can_judge_material


class BatchJudgeForm(forms.Form):
    rows = forms.CharField(
        label='评委名单',
        max_length=30000,
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='每行：姓名,邮箱。最多100人。已有账户只增加本届评委角色，不更改密码。不会发送邮件。',
    )

    def clean_rows(self):
        try:
            rows = list(csv.reader(io.StringIO(self.cleaned_data['rows']), strict=True))
        except csv.Error as error:
            raise ValidationError('名单格式无法读取，请使用姓名,邮箱。') from error
        rows = [row for row in rows if any(value.strip() for value in row)]
        if not rows or len(rows) > 100:
            raise ValidationError('请提供1至100行名单。')
        return rows


@sensitive_variables('password')
def provision_judges(hackathon, actor, rows):
    """One transaction per row; repeat requests never reset existing credentials."""
    results = []
    with transaction.atomic():
        h = services.lock_event(hackathon)
        if h.results_published:
            raise ValidationError('成绩已公布，不能增加评委。')
        for number, row in enumerate(rows, 1):
            result = {'row': number, 'name': '', 'email': '', 'password': ''}
            if len(row) != 2:
                result.update(status='error', message='需要姓名和邮箱两列。')
                results.append(result)
                continue
            name, email = (value.strip() for value in row)
            email = email.lower()
            result.update(name=name, email=email)
            try:
                validate_email(email)
                if not name or len(name) > 80 or len(email) > 190:
                    raise ValidationError('姓名需1至80字，邮箱不超过190字。')
                with transaction.atomic():
                    user = User.objects.filter(email__iexact=email).first()
                    created = user is None
                    if created:
                        password = secrets.token_urlsafe(18)
                        user = User.objects.create_user(
                            email=email,
                            fullname=name,
                            password=password,
                            locale='zh-hans',
                            timezone=h.event.timezone,
                        )
                    elif not user.is_active or user.is_staff or user.is_administrator or user.is_superuser:
                        raise ValidationError('账户已停用或具有管理权限，请在工作人员页面单独核实分配。')
                    assignment, assigned = StaffAssignment.objects.get_or_create(
                        hackathon=h,
                        user=user,
                        role='judge',
                    )
                    services.audit(h, actor, '批量分配评委', user.pk, '新账户' if created else '已有账户')
                    result.update(
                        status='created' if created else 'assigned' if assigned else 'existing',
                        message='已创建，仅授本届评委权限'
                        if created
                        else '已分配本届评委角色'
                        if assigned
                        else '已是本届评委，未重复创建',
                        password=password if created else '',
                    )
            except (ValidationError, IntegrityError) as error:
                result.update(
                    status='error',
                    message='；'.join(error.messages)
                    if isinstance(error, ValidationError)
                    else '账户并发创建，请重试本行。',
                )
            results.append(result)
    return results


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def batch_judges(request):
    form = BatchJudgeForm(request.POST if request.method == 'POST' else None)
    results = []
    if request.method == 'POST' and form.is_valid():
        try:
            results = provision_judges(request.hackathon, request.user, form.cleaned_data['rows'])
        except ValidationError as error:
            form.add_error(None, error)
    return page(request, 'batch_judges', form=form, results=results)


def readable_media(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related('project__team'), pk=asset_id, hackathon=request.hackathon)
    owner = request.participant and request.participant.team_id == asset.project.team_id
    if not (
        request.hackathon_organizer
        or (
            'judge' in request.hackathon_roles
            and can_judge_material(request.hackathon, request.user, asset.project_id, kind=asset.kind, asset=asset)
        )
        or owner
    ):
        raise Http404
    if asset.kind not in {'video', 'preliminary'} or Path(asset.filename).suffix.lower() not in {'.mp4', '.webm'}:
        raise Http404
    return asset


@event_view('login')
@require_safe
def media(request, asset_id):
    asset = readable_media(request, asset_id)
    return asset_response(request, asset, attachment=False)


@event_view('login')
@require_safe
def media_source(request, asset_id):
    """Fresh short-lived OSS GET; expiry recovery rechecks the same event permission."""
    asset = readable_media(request, asset_id)
    try:
        data = private_media_source(
            asset.file.storage,
            asset.file.name,
            asset.filename,
            url(request.hackathon, 'media', asset_id=asset.pk),
        )
    except FileNotFoundError as error:
        raise Http404('材料文件不可用，请联系主办方。') from error
    except STORAGE_ERRORS:
        return JsonResponse({'error': '存储暂时不可用，请稍后重试。'}, status=503)
    response = JsonResponse(data)
    response['Referrer-Policy'] = 'no-referrer'
    return response


def private_media_source(storage, name, filename, fallback_url):
    """Caller must authorize the immutable snapshot or current asset before calling."""
    backend = getattr(storage, 'backend', storage)
    source, transport = fallback_url, 'relay'
    if isinstance(backend, S3Storage):
        metadata = backend.head(name)
        expected_type = 'video/mp4' if filename.lower().endswith('.mp4') else 'video/webm'
        if metadata.get('ContentType', '').split(';')[0].strip().lower() != expected_type:
            return {'url': fallback_url, 'transport': 'relay', 'expires_in': 300}
        source = backend.client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': backend.bucket,
                'Key': backend.key(name),
                'ResponseContentDisposition': 'inline',
                'ResponseCacheControl': 'private, no-store',
            },
            ExpiresIn=300,
        )
        transport = 'direct'
    return {'url': source, 'transport': transport, 'expires_in': 300}
