import uuid

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from eventyay.hackathon import matching, services, social
from eventyay.hackathon.models import ChatMessage, ContactGrant, MatchIntent, SocialPreference, SocialReport
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
        .order_by('-updated')[:100]
    )
    threads = []
    for item in items:
        other = item.recipient if item.sender_id == p.pk else item.sender
        threads.append({'id': item.pk, 'name': other.name})
    return page(request, 'social_inbox', title='站内沟通', threads=threads)


@never_cache
@event_view('login')
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def conversation(request, intent_id):
    h = request.hackathon
    if not request.participant:
        return redirect(url(h, 'register'))
    p = request.participant
    with transaction.atomic():
        services.lock_event(h)
        item = get_object_or_404(MatchIntent.objects.select_related('sender', 'recipient'), hackathon=h, pk=intent_id)
        if p.pk not in {item.sender_id, item.recipient_id}:
            raise PermissionDenied
        other = item.recipient if item.sender_id == p.pk else item.sender
        try:
            social.chat_intent(h, p, intent_id)
            can_chat = True
        except PermissionDenied:
            can_chat = False
        if request.method == 'POST':
            action = request.POST.get('action', 'send')
            if not can_chat and (
                action not in {'report', 'block', 'close', 'share'}
                or (action == 'share' and request.POST.getlist('fields'))
            ):
                raise PermissionDenied
            try:
                if action == 'send':
                    try:
                        client_id = uuid.UUID(request.POST.get('client_id', ''))
                    except ValueError:
                        raise ValidationError('请刷新页面后重试。')
                    social.send(h, p, item, request.POST.get('body', ''), client_id)
                elif action == 'share':
                    fields = request.POST.getlist('fields')
                    if not set(fields) <= set(social.CONTACT_FIELDS):
                        raise ValidationError('无效联系方式字段。')
                    ContactGrant.objects.update_or_create(
                        hackathon=h, intent=item, owner=p, defaults={'fields': fields}
                    )
                elif action == 'block':
                    matching.set_block(h, request.user, other.pk)
                    return redirect(url(h, 'social-inbox'))
                elif action == 'close':
                    item.state = 'cancelled'
                    item.version += 1
                    item.save()
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
            else:
                if request.headers.get('X-Requested-With') == 'fetch':
                    return JsonResponse({'ok': True})
                return redirect(url(h, 'social-chat', intent_id=item.pk))
        if request.GET.get('poll') == '1':
            if not can_chat:
                raise PermissionDenied
            try:
                after = max(0, int(request.GET.get('after', '0')))
            except ValueError:
                return JsonResponse({'error': '无效游标'}, status=400)
            rows = list(
                ChatMessage.objects.filter(hackathon=h, intent=item, id__gt=after)
                .order_by('id')
                .values('id', 'body', 'author_id', 'created')[:100]
            )
            response = JsonResponse({'messages': rows, 'contacts': social.contacts(h, item, other)})
            response['Cache-Control'] = 'no-store'
            return response
    grant = ContactGrant.objects.filter(hackathon=h, intent=item, owner=p).first()
    return page(
        request,
        'social_chat',
        title='与' + other.name + '交流',
        thread=item,
        other=other,
        chat_messages=ChatMessage.objects.filter(hackathon=h, intent=item).order_by('-id')[:100] if can_chat else [],
        can_chat=can_chat,
        contacts=social.contacts(h, item, other) if can_chat else {},
        granted=grant.fields if grant else [],
        client_id=uuid.uuid4(),
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def reports(request):
    h = request.hackathon
    if request.method == 'POST':
        report = get_object_or_404(SocialReport, hackathon=h, pk=request.POST.get('report_id'))
        report.resolved = True
        report.save(update_fields=['resolved'])
        services.audit(h, request.user, '处理沟通举报', report.pk)
        return redirect(url(h, 'social-reports'))
    return page(
        request,
        'social_reports',
        title='沟通举报',
        reports=SocialReport.objects.filter(hackathon=h, resolved=False)
        .select_related('reporter')
        .order_by('created')[:100],
    )
