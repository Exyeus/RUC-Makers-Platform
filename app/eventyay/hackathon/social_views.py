import uuid
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from eventyay.hackathon import matching, services, social
from eventyay.hackathon.models import (
    AuditEntry,
    ChatMessage,
    CommunityResponse,
    ContactGrant,
    MatchIntent,
    SocialPreference,
    SocialReport,
)
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url


class PrivacyForm(forms.Form):
    allow_requests = forms.BooleanField(label='接收新的交流申请', required=False, initial=True)
    allow_chat = forms.BooleanField(label='允许已同意的伙伴继续私聊', required=False, initial=True)
    visible_fields = forms.MultipleChoiceField(
        label='个人资料在组队目录 / 推荐页可展示的字段',
        choices=[
            ('tagline', '一句话介绍'),
            ('bio', '项目与自我介绍'),
            ('skills', '技能'),
            ('keywords', '个人标签'),
            ('school', '学校 / 组织'),
        ],
        required=False,
        initial=social.PUBLIC_FIELDS,
        widget=forms.CheckboxSelectMultiple,
    )
    wechat = forms.CharField(label='微信（单独填报，不自动读取报名联系方式）', max_length=100, required=False)
    phone = forms.CharField(label='电话', max_length=100, required=False)
    email = forms.EmailField(label='交流邮箱', required=False)


@never_cache
@event_view('login')
@require_http_methods(['GET', 'POST'])
def privacy(request):
    h = request.hackathon
    if not request.participant:
        return redirect(url(h, 'register'))
    p = request.participant
    pref = social.preference(p)
    initial = {'visible_fields': [k for k in social.PUBLIC_FIELDS if not pref or k not in pref.hidden_fields]}
    if pref:
        initial.update(allow_requests=pref.allow_requests, allow_chat=pref.allow_chat, **pref.contacts)
    form = PrivacyForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            services.lock_event(h)
            data = form.cleaned_data
            SocialPreference.objects.update_or_create(
                hackathon=h,
                participant=p,
                defaults={
                    'allow_requests': data['allow_requests'],
                    'allow_chat': data['allow_chat'],
                    'hidden_fields': [k for k in social.PUBLIC_FIELDS if k not in data['visible_fields']],
                    'contacts': {k: data[k] for k in ('wechat', 'phone', 'email')},
                },
            )
        messages.success(request, '隐私设置已保存。联系方式仅向你在具体会话中授权的对象展示。')
        return redirect(url(h, 'social-privacy'))
    return page(
        request,
        'form',
        form=form,
        title='展示与沟通隐私',
        button='保存隐私设置',
        subtitle='姓名用于识别交流对象。字段隐藏适用于组队目录及推荐页，不会删除你已主动发布的帖子或消息。撤销授权不能收回对方此前保存的内容。',
    )


@never_cache
@event_view('login')
@require_http_methods(['GET'])
def inbox(request):
    h = request.hackathon
    if not request.participant:
        return redirect(url(h, 'register'))
    p = request.participant
    items = (
        MatchIntent.objects.filter(hackathon=h, state__in=['accepted', 'cancelled'])
        .filter(Q(sender=p) | Q(recipient=p))
        .select_related('sender', 'recipient')
        .order_by('-updated', '-pk')
    )
    thread_page = Paginator(items, 50).get_page(request.GET.get('page'))
    threads = []
    for item in thread_page:
        other = item.recipient if item.sender_id == p.pk else item.sender
        threads.append({'id': item.pk, 'name': other.name})
    return page(request, 'social_inbox', title='站内沟通', threads=threads, thread_page=thread_page)


def conversation_access(h, p, intent_id):
    item = get_object_or_404(MatchIntent.objects.select_related('sender', 'recipient'), hackathon=h, pk=intent_id)
    if p.pk not in {item.sender_id, item.recipient_id}:
        raise PermissionDenied
    other = item.recipient if item.sender_id == p.pk else item.sender
    try:
        social.chat_intent(h, p, intent_id)
        can_chat = True
    except PermissionDenied:
        can_chat = False
    return item, other, can_chat


def cursor_value(raw):
    try:
        value = int(raw)
    except (ValueError, TypeError):
        raise ValidationError('无效消息游标。') from None
    if not 0 <= value <= 9223372036854775807:
        raise ValidationError('无效消息游标。')
    return value


def conversation_write(request, item, other, can_chat):
    """Called only after acquiring the event lock and rechecking current access."""
    h, p = request.hackathon, request.participant
    action = request.POST.get('action', 'send')
    if not can_chat and (
        action not in {'report', 'block', 'close', 'share'} or (action == 'share' and request.POST.getlist('fields'))
    ):
        raise PermissionDenied
    try:
        if action == 'send':
            try:
                client_id = uuid.UUID(request.POST.get('client_id', ''))
            except (ValueError, TypeError, AttributeError):
                raise ValidationError('请刷新页面后重试。') from None
            social.send(h, p, item, request.POST.get('body', ''), client_id)
        elif action == 'share':
            fields = request.POST.getlist('fields')
            if not set(fields) <= set(social.CONTACT_FIELDS):
                raise ValidationError('无效联系方式字段。')
            ContactGrant.objects.update_or_create(hackathon=h, intent=item, owner=p, defaults={'fields': fields})
        elif action == 'block':
            matching.set_block(h, request.user, other.pk)
            return redirect(url(h, 'social-inbox'))
        elif action == 'close':
            item.state = 'cancelled'
            item.version += 1
            item.save(update_fields=['state', 'version', 'updated'])
            return redirect(url(h, 'social-inbox'))
        elif action == 'report':
            reason = request.POST.get('reason', '').strip()
            if not reason or len(reason) > 1000:
                raise ValidationError('请填写1–1000字举报说明。')
            social.rate(SocialReport.objects.filter(hackathon=h, reporter=p), 5, 3600)
            SocialReport.objects.create(
                hackathon=h, reporter=p, target_type='chat', target_id=str(item.pk), reason=reason
            )
        else:
            raise ValidationError('无效操作。')
    except ValidationError as error:
        if request.headers.get('X-Requested-With') == 'fetch':
            return JsonResponse({'error': '；'.join(error.messages)}, status=422)
        messages.error(request, '；'.join(error.messages))
        return None
    if request.headers.get('X-Requested-With') == 'fetch':
        # The server supplies the next retry ID even in HTTP browsers without randomUUID.
        return JsonResponse({'ok': True, 'next_client_id': str(uuid.uuid4())})
    return redirect(url(h, 'social-chat', intent_id=item.pk))


@never_cache
@event_view('login')
@require_http_methods(['GET', 'POST'])
def conversation(request, intent_id):
    h = request.hackathon
    if not request.participant:
        return redirect(url(h, 'register'))
    p = request.participant
    if request.method == 'POST':
        with transaction.atomic():
            services.lock_event(h)
            item, other, can_chat = conversation_access(h, p, intent_id)
            response = conversation_write(request, item, other, can_chat)
            if response is not None:
                return response
    # Ordinary reads/polls do not acquire the activity-wide roster/upload lock.
    item, other, can_chat = conversation_access(h, p, intent_id)
    if request.GET.get('poll') == '1':
        if not can_chat:
            raise PermissionDenied
        try:
            after = cursor_value(request.GET.get('after', '0'))
        except ValidationError:
            return JsonResponse({'error': '无效游标'}, status=400)
        rows = list(
            ChatMessage.objects.filter(hackathon=h, intent=item, id__gt=after)
            .order_by('id')
            .values('id', 'body', 'author_id', 'created')[:101]
        )
        return JsonResponse(
            {'messages': rows[:100], 'has_more': len(rows) > 100, 'contacts': social.contacts(h, item, other)}
        )
    chat_messages, has_older, showing_history, oldest_id, poll_after = [], False, False, None, 0
    if can_chat:
        history = ChatMessage.objects.filter(hackathon=h, intent=item)
        poll_after = history.order_by('-id').values_list('id', flat=True).first() or 0
        before = request.GET.get('before')
        if before is not None:
            try:
                before = cursor_value(before)
            except ValidationError:
                return JsonResponse({'error': '无效游标'}, status=400)
            history = history.filter(id__lt=before)
            showing_history = True
        chat_messages = list(history.order_by('-id')[:100])
        if chat_messages:
            oldest_id = chat_messages[-1].pk
            has_older = history.filter(id__lt=oldest_id).exists()
        # On the newest page, rows arriving between these reads are picked up by polling.
        if not showing_history:
            poll_after = max((message.pk for message in chat_messages), default=0)
    grant = ContactGrant.objects.filter(hackathon=h, intent=item, owner=p).first()
    return page(
        request,
        'social_chat',
        title='与' + other.name + '交流',
        thread=item,
        other=other,
        chat_messages=chat_messages,
        has_older=has_older,
        oldest_id=oldest_id,
        showing_history=showing_history,
        poll_after=poll_after,
        can_chat=can_chat,
        contacts=social.contacts(h, item, other) if can_chat else {},
        granted=grant.fields if grant else [],
        client_id=uuid.uuid4(),
    )


def report_evidence(h, report, before=None):
    """Resolve only the target named by a submitted report, never an arbitrary conversation."""
    evidence = {'kind': report.target_type, 'available': False}
    if report.target_type == 'comment' and report.target_id.isdigit():
        comment = (
            CommunityResponse.objects.filter(hackathon=h, pk=report.target_id)
            .select_related('author', 'post__author')
            .first()
        )
        if comment and (not comment.private or report.reporter_id in {comment.author_id, comment.post.author_id}):
            later = (
                CommunityResponse.objects.filter(hackathon=h, post=comment.post)
                .filter(Q(created__gt=comment.created) | Q(created=comment.created, id__gt=comment.pk))
                .count()
            )
            location = url(h, 'post-detail', post_id=comment.post_id)
            evidence.update(
                available=True,
                comment=comment,
                target_url=f'{location}?page={later // 50 + 1}#response-{comment.pk}',
            )
    elif report.target_type == 'chat':
        try:
            intent_id = uuid.UUID(report.target_id)
        except (ValueError, TypeError, AttributeError):
            return evidence
        intent = MatchIntent.objects.filter(hackathon=h, pk=intent_id).select_related('sender', 'recipient').first()
        if intent and report.reporter_id in {intent.sender_id, intent.recipient_id}:
            # Later private messages are outside the submitted report's context.
            history = ChatMessage.objects.filter(hackathon=h, intent=intent, created__lte=report.created)
            if before is not None:
                history = history.filter(id__lt=cursor_value(before))
            rows = list(history.select_related('author').order_by('-id')[:100])
            oldest = rows[-1].pk if rows else None
            evidence.update(
                available=True,
                intent=intent,
                chat_messages=rows,
                has_older=oldest is not None and history.filter(id__lt=oldest).exists(),
                oldest_id=oldest,
            )
    return evidence


@never_cache
@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def reports(request):
    h = request.hackathon
    if request.method == 'POST':
        report = get_object_or_404(SocialReport, hackathon=h, pk=request.POST.get('report_id'))
        resolution = request.POST.get('resolution', '').strip()
        if not resolution or len(resolution) > 2000:
            messages.error(request, '请填写1–2000字处理说明。')
        else:
            with transaction.atomic():
                report = SocialReport.objects.select_for_update().get(hackathon=h, pk=report.pk)
                if not report.resolved:
                    report.resolved = True
                    report.save(update_fields=['resolved'])
                    services.audit(h, request.user, '处理沟通举报', report.pk, resolution)
            messages.success(request, '处理结果已记录。')
        return redirect(url(h, 'social-reports') + '?' + urlencode({'report_id': report.pk}))
    selected = None
    evidence = None
    report_id = request.GET.get('report_id')
    if report_id is not None:
        if not report_id.isdigit():
            raise Http404
        selected = get_object_or_404(SocialReport.objects.select_related('reporter'), hackathon=h, pk=report_id)
        try:
            evidence = report_evidence(h, selected, request.GET.get('before'))
        except ValidationError:
            return JsonResponse({'error': '无效游标'}, status=400)
        services.audit(h, request.user, '查看沟通举报证据', selected.pk, f'{selected.target_type}:{selected.target_id}')
    return page(
        request,
        'social_reports',
        title='沟通举报',
        reports=SocialReport.objects.filter(hackathon=h, resolved=False)
        .select_related('reporter')
        .order_by('created')[:100],
        selected_report=selected,
        evidence=evidence,
        resolution_history=(
            AuditEntry.objects.filter(hackathon=h, action='处理沟通举报', target=str(selected.pk))
            .select_related('actor')
            .order_by('-created')[:10]
            if selected
            else []
        ),
    )
