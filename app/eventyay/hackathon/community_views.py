from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from eventyay.hackathon import services, social
from eventyay.hackathon.forms import CommunityPostForm, CommunityResponseForm
from eventyay.hackathon.matching import blocked, excluded_ids
from eventyay.hackathon.models import (
    BonusClaim,
    BonusRule,
    CommunityPost,
    CommunityResponse,
    PostReaction,
    SocialReport,
)
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url


@event_view('login')
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def post_edit(request, post_id=None):
    h, participant = request.hackathon, request.participant
    if request.method == 'POST':
        services.lock_event(h)
    if not participant:
        return redirect(url(h, 'register'))
    post = get_object_or_404(CommunityPost, hackathon=h, pk=post_id, author=participant) if post_id else None
    kind = request.GET.get('kind', '')
    initial = {'kind': kind} if not post and kind in CommunityPost.Kind.values else {}
    form = CommunityPostForm(request.POST if request.method == 'POST' else None, instance=post, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if not post:
            try:
                social.rate(CommunityPost.objects.filter(hackathon=h, author=participant), 10, 3600)
            except ValidationError as error:
                form.add_error(None, error)
                return page(request, 'form', form=form, title='分享交流卡片', button='保存')
        post = form.save(commit=False)
        post.hackathon, post.author = h, participant
        post.save()
        services.audit(h, request.user, '保存交流卡片', post.pk)
        return redirect(url(h, 'post-detail', post_id=post.pk))
    return page(
        request,
        'form',
        form=form,
        title='分享一点，认识一个新伙伴',
        subtitle='一个问题、一件小作品或一次 20 分钟的共创都可以。发布和回应不自动加分。取消公开可保存为自己的草稿。',
        button='保存交流卡片',
    )


@event_view('login')
@require_http_methods(['GET', 'POST'])
def post_detail(request, post_id):
    h, participant = request.hackathon, request.participant
    post = get_object_or_404(CommunityPost.objects.select_related('author'), hackathon=h, pk=post_id)
    if (
        not post.is_public
        and not request.hackathon_organizer
        and post.author_id != (participant.pk if participant else None)
    ):
        raise Http404
    if participant and not request.hackathon_organizer and blocked(participant, post.author):
        raise Http404
    form = CommunityResponseForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        action = request.POST.get('action', 'comment')
        moderation = action == 'remove' and request.hackathon_organizer
        if not participant and not moderation:
            return redirect(url(h, 'register'))
        with transaction.atomic():
            services.lock_event(h)
            post.refresh_from_db()
            if participant and not moderation and blocked(participant, post.author):
                raise PermissionDenied
            for key in ('response_id', 'parent_id'):
                if request.POST.get(key) and not request.POST[key].isdigit():
                    raise Http404
            try:
                if action == 'remove':
                    response = get_object_or_404(
                        CommunityResponse, hackathon=h, post=post, pk=request.POST.get('response_id')
                    )
                    if not request.hackathon_organizer and response.author_id != participant.pk:
                        raise PermissionDenied
                    response.body = ''
                    response.removed = True
                    response.save(update_fields=['body', 'removed'])
                    services.audit(
                        h, request.user, '撤回交流回应', response.pk, '主办处理' if moderation else '本人撤回'
                    )
                elif action == 'report':
                    target = get_object_or_404(
                        CommunityResponse, hackathon=h, post=post, pk=request.POST.get('response_id'), removed=False
                    )
                    if target.private and participant.pk not in {target.author_id, post.author_id}:
                        raise PermissionDenied
                    reason = request.POST.get('reason', '').strip()
                    if not reason or len(reason) > 1000:
                        raise ValidationError('请填写1–1000字举报说明。')
                    social.rate(SocialReport.objects.filter(hackathon=h, reporter=participant), 5, 3600)
                    SocialReport.objects.create(
                        hackathon=h,
                        reporter=participant,
                        target_type='comment',
                        target_id=str(target.pk),
                        reason=reason,
                    )
                elif not post.is_public or not post.is_open:
                    raise ValidationError('这张卡片暂不接受互动。')
                elif action in {'like', 'unlike'}:
                    if action == 'like':
                        social.rate(PostReaction.objects.filter(hackathon=h, author=participant), 30, 60)
                        PostReaction.objects.get_or_create(hackathon=h, post=post, author=participant)
                    else:
                        PostReaction.objects.filter(hackathon=h, post=post, author=participant).delete()
                elif action == 'comment' and form.is_valid():
                    social.rate(CommunityResponse.objects.filter(hackathon=h, author=participant), 10, 60)
                    social.rate(CommunityResponse.objects.filter(hackathon=h, author=participant), 100, 86400)
                    response = form.save(commit=False)
                    parent_id = request.POST.get('parent_id')
                    if parent_id:
                        parent = get_object_or_404(
                            CommunityResponse, hackathon=h, post=post, pk=parent_id, private=False, removed=False
                        )
                        if blocked(participant, parent.author):
                            raise PermissionDenied
                        response.parent = parent
                    response.hackathon, response.post, response.author = h, post, participant
                    response.save()
                elif action != 'comment':
                    raise ValidationError('无效互动操作。')
                else:
                    return page(request, 'community_post', post=post, responses=[], form=form, title=post.title)
            except ValidationError as error:
                messages.error(request, '；'.join(error.messages))
            else:
                messages.success(request, '已保存。')
        return redirect(url(h, 'post-detail', post_id=post.pk))
    responses = CommunityResponse.objects.filter(hackathon=h, post=post).select_related('author', 'parent')
    if participant and not request.hackathon_organizer:
        responses = responses.exclude(author_id__in=excluded_ids(h, participant))
    if not request.hackathon_organizer and post.author_id != (participant.pk if participant else None):
        responses = responses.filter(Q(private=False) | Q(author=participant))
    response_page = Paginator(responses.order_by('-created', '-id'), 50).get_page(request.GET.get('page'))
    likes = PostReaction.objects.filter(hackathon=h, post=post)
    if participant:
        likes = likes.exclude(author_id__in=excluded_ids(h, participant))
    return page(
        request,
        'community_post',
        post=post,
        responses=response_page,
        form=form,
        title=post.title,
        response_page=response_page,
        like_count=likes.count(),
        liked=likes.filter(author=participant).exists() if participant else False,
    )


class ClaimForm(forms.Form):
    rule = forms.ModelChoiceField(label='加分事项', queryset=BonusRule.objects.none())
    evidence = forms.CharField(label='完成说明与证明链接', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, hackathon, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rule'].queryset = BonusRule.objects.filter(hackathon=hackathon, active=True)
        self.fields['rule'].label_from_instance = lambda rule: f'{rule.title}（{rule.points} 分）'


class ReviewClaimForm(forms.Form):
    claim_id = forms.IntegerField(min_value=1)
    decision = forms.ChoiceField(choices=[('approved', '通过'), ('rejected', '不通过')])
    note = forms.CharField(label='审核说明', max_length=500)


@event_view()
@require_http_methods(['GET', 'POST'])
def bonus(request):
    h, participant = request.hackathon, request.participant
    form = ClaimForm(request.POST if request.method == 'POST' else None, hackathon=h)
    if request.method == 'POST':
        if request.POST.get('operation') == 'review':
            if not request.hackathon_organizer:
                raise PermissionDenied
            review = ReviewClaimForm(request.POST)
            if review.is_valid():
                with transaction.atomic():
                    services.lock_event(h)
                    claim = get_object_or_404(
                        BonusClaim.objects.select_for_update().select_related('rule'),
                        hackathon=h,
                        pk=review.cleaned_data['claim_id'],
                    )
                    if claim.state == BonusClaim.State.PENDING:
                        claim.state = review.cleaned_data['decision']
                        claim.review_note = review.cleaned_data['note']
                        claim.reviewer = request.user
                        claim.awarded_points = claim.rule.points if claim.state == BonusClaim.State.APPROVED else 0
                        claim.save()
                        services.audit(h, request.user, '审核黑客松加分申请', claim.pk, claim.review_note)
                    else:
                        messages.error(request, '该申请已经审核，请刷新后查看结果。')
                return redirect(url(h, 'bonus'))
            messages.error(request, '请填写有效申请、审核结果和审核说明。')
        elif not participant:
            return redirect(url(h, 'register'))
        elif form.is_valid():
            with transaction.atomic():
                services.lock_event(h)
                rule = get_object_or_404(BonusRule, hackathon=h, pk=form.cleaned_data['rule'].pk, active=True)
                _, created = BonusClaim.objects.get_or_create(
                    hackathon=h,
                    participant=participant,
                    rule=rule,
                    defaults={'evidence': form.cleaned_data['evidence']},
                )
            messages.success(request, '申请已提交，等待主办方审核。' if created else '此事项已经申请，请查看审核进度。')
            return redirect(url(h, 'bonus'))
    own_claims = (
        BonusClaim.objects.filter(hackathon=h, participant=participant) if participant else BonusClaim.objects.none()
    )
    pending = (
        BonusClaim.objects.filter(hackathon=h, state='pending')
        if request.hackathon_organizer
        else BonusClaim.objects.none()
    )
    return page(
        request,
        'bonus',
        form=form,
        title='加分规则',
        rules=BonusRule.objects.filter(hackathon=h, active=True),
        claims=own_claims.select_related('rule'),
        bonus_total=own_claims.aggregate(total=Sum('awarded_points'))['total'] or 0,
        pending_claims=pending.select_related('participant', 'rule'),
    )
