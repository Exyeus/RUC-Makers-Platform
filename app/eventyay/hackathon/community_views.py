from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from eventyay.hackathon import services
from eventyay.hackathon.forms import CommunityPostForm, CommunityResponseForm
from eventyay.hackathon.matching import blocked
from eventyay.hackathon.models import BonusClaim, BonusRule, CommunityPost, CommunityResponse
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url


@event_view('login')
@require_http_methods(['GET', 'POST'])
def post_edit(request, post_id=None):
    h, participant = request.hackathon, request.participant
    if not participant:
        return redirect(url(h, 'register'))
    post = get_object_or_404(CommunityPost, hackathon=h, pk=post_id, author=participant) if post_id else None
    form = CommunityPostForm(request.POST if request.method == 'POST' else None, instance=post)
    if request.method == 'POST' and form.is_valid():
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
    if participant and blocked(participant, post.author):
        raise Http404
    form = CommunityResponseForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if not participant:
            return redirect(url(h, 'register'))
        if not post.is_open:
            form.add_error(None, '这张卡片已关闭回应。')
        elif not post.is_public:
            form.add_error(None, '草稿暂不接受回应。')
        elif form.is_valid():
            response = form.save(commit=False)
            response.hackathon, response.post, response.author = h, post, participant
            response.save()
            messages.success(request, '回应已送达。你们可以先交流，再决定是否合作。')
            return redirect(url(h, 'post-detail', post_id=post.pk))
    responses = CommunityResponse.objects.filter(hackathon=h, post=post).select_related('author')
    if not request.hackathon_organizer and post.author_id != (participant.pk if participant else None):
        responses = responses.filter(Q(private=False) | Q(author=participant))
    return page(request, 'community_post', post=post, responses=responses, form=form)


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
                        services.audit(h, request.user, '审核创客营加分申请', claim.pk, claim.review_note)
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
        rules=BonusRule.objects.filter(hackathon=h, active=True),
        claims=own_claims.select_related('rule'),
        bonus_total=own_claims.aggregate(total=Sum('awarded_points'))['total'] or 0,
        pending_claims=pending.select_related('participant', 'rule'),
    )
