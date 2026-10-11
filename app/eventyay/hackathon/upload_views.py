import logging
from functools import wraps

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from eventyay.hackathon import uploads
from eventyay.hackathon.constants import CHUNK_SIZE
from eventyay.hackathon.forms import UploadStartForm
from eventyay.hackathon.models import Project, UploadSession
from eventyay.hackathon.permissions import event_view


logger = logging.getLogger(__name__)


def record_error(request, upload_id, message):
    if upload_id:
        UploadSession.objects.filter(
            pk=upload_id,
            hackathon=request.hackathon,
            user=request.user,
            state='uploading',
        ).update(last_error=message[:500])


def upload_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except uploads.UploadQueued as error:
            record_error(request, kwargs.get('upload_id'), ' '.join(error.messages))
            response = JsonResponse({'error': ' '.join(error.messages), 'queued': True}, status=429)
            response['Retry-After'] = '5'
            return response
        except uploads.UploadConflict as error:
            record_error(request, kwargs.get('upload_id'), ' '.join(error.messages))
            return JsonResponse({'error': ' '.join(error.messages)}, status=409)
        except ValidationError as error:
            record_error(request, kwargs.get('upload_id'), ' '.join(error.messages))
            return JsonResponse({'error': ' '.join(error.messages)}, status=400)
        except OSError:
            logger.exception('Hackathon upload storage operation failed')
            record_error(request, kwargs.get('upload_id'), '存储暂时不可用，请稍后重试或联系技术人员。')
            return JsonResponse({'error': '存储暂时不可用，请稍后重试或联系主办方。已保存的材料不受影响。'}, status=503)

    return wrapped


def session_data(session):
    kwargs = {'hackathon_slug': session.hackathon.slug, 'upload_id': session.pk}
    data = {
        'id': str(session.pk),
        'state': session.state,
        'offset': session.offset,
        'size': session.size,
        'chunk_size': CHUNK_SIZE,
        'checksum': session.checksum,
        'kind': session.kind,
        'filename': session.original_filename,
        'received_at': session.created.isoformat(),
        'deadline_at': session.deadline_at.isoformat() if session.deadline_at else None,
        'client_clicked_at': session.client_clicked_at.isoformat() if session.client_clicked_at else None,
        'expected_checksum': session.expected_checksum,
        'checksum_bound_at': session.checksum_bound_at.isoformat() if session.checksum_bound_at else None,
        'expires_at': min(session.expires_at, session.hackathon.freeze_at).isoformat(),
        'last_error': session.last_error,
        'admit_url': reverse('hackathon:upload-admit', kwargs=kwargs),
        'status_url': reverse('hackathon:upload-status', kwargs=kwargs),
        'chunk_url': reverse('hackathon:upload-chunk', kwargs=kwargs),
        'finish_url': reverse('hackathon:upload-finish', kwargs=kwargs),
        'cancel_url': reverse('hackathon:upload-cancel', kwargs=kwargs),
    }
    evidence = {
        key: data[key]
        for key in (
            'id',
            'filename',
            'size',
            'received_at',
            'client_clicked_at',
            'expected_checksum',
            'checksum_bound_at',
            'expires_at',
            'deadline_at',
        )
    }
    evidence['project_id'] = str(session.project_id)
    evidence['user_id'] = str(session.user_id)
    evidence['hackathon_id'] = session.hackathon_id
    data['receipt'] = signing.dumps(evidence, salt='hackathon.upload-receipt', compress=True)
    if session.state == 'uploading':
        if session.expires_at <= timezone.now() or session.hackathon.freeze_at <= timezone.now():
            data['state'] = 'expired'
        elif not session.expected_checksum:
            data['state'] = 'hashing'
        elif not session.lease_expires_at or session.lease_expires_at <= timezone.now():
            data['state'] = 'queued'
    if session.state == 'complete' and session.asset_id and session.asset.checksum != session.checksum:
        data['state'] = 'superseded'
    elif session.state == 'complete' and session.asset_id:
        data.update(
            {
                'revision': Project.objects.get(pk=session.project_id).revision,
                'stored_filename': session.asset.filename,
                'download_url': reverse(
                    'hackathon:download',
                    kwargs={
                        'hackathon_slug': session.hackathon.slug,
                        'asset_id': session.asset_id,
                    },
                ),
                'preview_url': reverse(
                    'hackathon:asset-preview',
                    kwargs={
                        'hackathon_slug': session.hackathon.slug,
                        'asset_id': session.asset_id,
                    },
                )
                if session.original_filename.lower().endswith('.zip')
                else None,
            }
        )
    return data


def get_session(request, upload_id):
    session = get_object_or_404(
        UploadSession.objects.select_related('hackathon', 'project__team'),
        pk=upload_id,
        hackathon=request.hackathon,
        user=request.user,
    )
    uploads.require_owner(request.hackathon, request.user, session.project)
    return session


@event_view('login')
@require_POST
@upload_errors
def start(request):
    form = UploadStartForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {'error': ' '.join(message for errors in form.errors.values() for message in errors)}, status=400
        )
    participant = request.participant
    project = get_object_or_404(
        Project.objects.select_related('team'),
        hackathon=request.hackathon,
        team_id=participant.team_id if participant else None,
    )
    session = uploads.start_upload(request.hackathon, request.user, project, form.cleaned_data)
    return JsonResponse(session_data(session))


@event_view('login')
@require_GET
def status(request, upload_id):
    response = JsonResponse(session_data(get_session(request, upload_id)))
    response['Cache-Control'] = 'no-store'
    if request.GET.get('download') == '1':
        response['Content-Disposition'] = f'attachment; filename="upload-{upload_id}.json"'
    return response


@event_view('login')
@require_POST
@upload_errors
def admit(request, upload_id):
    return JsonResponse(session_data(uploads.admit_upload(get_session(request, upload_id))))


@event_view('login')
@require_POST
@upload_errors
def chunk(request, upload_id):
    session = get_session(request, upload_id)
    try:
        offset = int(request.headers.get('Upload-Offset', ''))
        length = int(request.headers.get('Content-Length', ''))
    except ValueError:
        return JsonResponse({'error': '缺少有效的分块长度或偏移量。'}, status=400)
    if request.content_type != 'application/octet-stream':
        return JsonResponse({'error': '请使用二进制分块上传。'}, status=400)
    uploads.admit_upload(session)
    session = uploads.append_chunk(session, request, offset, length)
    return JsonResponse(session_data(session))


@event_view('login')
@require_POST
@upload_errors
def finish(request, upload_id):
    session = get_session(request, upload_id)
    if session.state == 'uploading':
        uploads.admit_upload(session)
    return JsonResponse(session_data(uploads.finish_upload(session)))


@event_view('login')
@require_POST
@upload_errors
def cancel(request, upload_id):
    return JsonResponse(session_data(uploads.cancel_upload(get_session(request, upload_id))))
