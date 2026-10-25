import json
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import serializers

from eventyay.hackathon import matching
from eventyay.hackathon.matching_forms import IdeaForm, ProfileForm, ProposalForm, profile_initial
from eventyay.hackathon.models import (
    Idea,
    MatchingContact,
    MatchingFeedback,
    MatchingProfile,
    MatchingReceipt,
    MatchIntent,
    Participant,
    RecommendationRun,
    Team,
    TeamProposal,
)
from eventyay.hackathon.permissions import event_view
from eventyay.hackathon.views import page, url


class IntentInput(serializers.Serializer):
    recipient_id = serializers.UUIDField()
    team_id = serializers.UUIDField(required=False, allow_null=True)
    message = serializers.CharField(max_length=500)


class IntentDecisionInput(serializers.Serializer):
    intent_id = serializers.UUIDField()
    decision = serializers.ChoiceField(choices=['accept', 'decline', 'cancel'])
    version = serializers.IntegerField(min_value=1)


class ProposalInput(serializers.Serializer):
    member_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=20)
    idea_id = serializers.UUIDField()
    track_id = serializers.IntegerField(min_value=1)
    team_name = serializers.CharField(max_length=80)
    agreement = serializers.CharField(max_length=2000)
    target_team_id = serializers.UUIDField(required=False, allow_null=True)


class ConsentInput(serializers.Serializer):
    proposal_id = serializers.UUIDField()
    proposal_hash = serializers.RegexField(r'^[a-f0-9]{64}$')
    decision = serializers.ChoiceField(choices=['accept', 'decline'])


class BlockInput(serializers.Serializer):
    target_id = serializers.UUIDField()
    enabled = serializers.BooleanField(default=True)


class FeedbackInput(serializers.Serializer):
    target_id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=['people', 'teams', 'ideas'])
    decision = serializers.ChoiceField(choices=['save', 'dismiss'])


class ConfigureInput(serializers.Serializer):
    enabled = serializers.BooleanField(default=False)
    roster = serializers.BooleanField(default=False)
    single = serializers.BooleanField(default=False)
    idea_min_chars = serializers.IntegerField(min_value=0, max_value=3000)
    team_size = serializers.IntegerField(min_value=1, max_value=20)
    capacity = serializers.IntegerField(min_value=1, max_value=10000, default=150)


class AdmissionInput(serializers.Serializer):
    participant_id = serializers.UUIDField()
    admission_state = serializers.ChoiceField(choices=['pending', 'admitted', 'waitlisted', 'withdrawn'])


class TransferInput(serializers.Serializer):
    participant_id = serializers.UUIDField()


class EmptyInput(serializers.Serializer):
    pass


INPUTS = {
    'transfer': TransferInput,
    'configure': ConfigureInput,
    'admission': AdmissionInput,
    'delete-profile': EmptyInput,
    'intents': IntentInput,
    'intent-decision': IntentDecisionInput,
    'proposals': ProposalInput,
    'consents': ConsentInput,
    'block': BlockInput,
    'feedback': FeedbackInput,
    'leave': EmptyInput,
    'freeze': EmptyInput,
    'matching-runs': EmptyInput,
}


def dispatch(h, actor, operation, data):
    if operation == 'transfer':
        return matching.transfer_captain(h, actor, data['participant_id'])
    if operation == 'configure':
        return matching.configure(
            h,
            actor,
            data['enabled'],
            data['roster'],
            data['single'],
            data['idea_min_chars'],
            data['team_size'],
            data['capacity'],
        )
    if operation == 'admission':
        return matching.set_admission(h, actor, data['participant_id'], data['admission_state'])
    if operation == 'delete-profile':
        return matching.delete_profile(h, actor)
    if operation == 'intents':
        return matching.send_intent(h, actor, data['recipient_id'], data['message'], data.get('team_id'))
    if operation == 'intent-decision':
        return matching.decide_intent(h, actor, data['intent_id'], data['decision'], data['version'])
    if operation == 'proposals':
        return matching.create_proposal(
            h,
            actor,
            data['member_ids'],
            data['idea_id'],
            data['track_id'],
            data['team_name'],
            data['agreement'],
            data.get('target_team_id'),
        )
    if operation == 'consents':
        return matching.consent_proposal(h, actor, data['proposal_id'], data['proposal_hash'], data['decision'])
    if operation == 'block':
        return matching.set_block(h, actor, data['target_id'], data['enabled'])
    if operation == 'feedback':
        p = matching.own_person(h, actor)
        if data['target_type'] == 'people':
            target = matching.person(h, data['target_id'])
            if not matching.visible_to(p, target):
                raise Http404
        elif data['target_type'] == 'teams':
            get_object_or_404(Team, hackathon=h, pk=data['target_id'], recruiting=True)
        else:
            get_object_or_404(Idea, hackathon=h, pk=data['target_id'], state='open')
        MatchingFeedback.objects.update_or_create(
            hackathon=h,
            participant=p,
            target_id=data['target_id'],
            target_type=data['target_type'],
            defaults={'decision': data['decision']},
        )
        return {'state': 'saved'}
    if operation == 'leave':
        return matching.leave(h, actor)
    if operation == 'freeze':
        return matching.freeze(h, actor)
    if operation == 'matching-runs':
        return matching.batch_suggestions(h, actor)
    raise Http404


def read_payload(h, actor, operation, params):
    p = matching.own_person(h, actor)
    if operation in {'people', 'teams', 'ideas'}:
        excluded = matching.excluded_ids(h, p)
    if operation == 'people':
        candidates = (
            Participant.objects.filter(hackathon=h, matching_profile__opt_in=True, matching_profile__visibility='event')
            .exclude(pk__in=excluded)
            .select_related('matching_profile')
        )
        query = params.get('q', '').strip()
        if query:
            candidates = candidates.filter(
                Q(name__icontains=query) | Q(bio__icontains=query) | Q(skills__icontains=query)
            )
        return {'people': [matching.public_person(candidate, candidate.matching_profile) for candidate in candidates]}
    if operation == 'teams':
        return {
            'teams': [
                {
                    'id': str(t.pk),
                    'name': t.name,
                    'intro': t.intro,
                    'needs': t.needs,
                    'member_count': t.member_count,
                    'remaining': h.team_size - t.member_count,
                    'roster_version': t.roster_version,
                    'recipient_id': str(t.captain_id),
                }
                for t in Team.objects.filter(
                    hackathon=h, recruiting=True, state='recruiting', captain__matching_profile__opt_in=True
                )
                .exclude(captain__matching_profile__visibility='paused')
                .exclude(members__pk__in=excluded)
                .annotate(member_count=Count('members'))
                .select_related('captain')
                if t.member_count < h.team_size
            ]
        }
    if operation == 'recommendations':
        return matching.recommendations(h, p, params.get('target', 'people'))
    if operation == 'profile':
        profile = matching.profile_for(p)
        contact = MatchingContact.objects.filter(hackathon=h, participant=p).first()
        return profile_initial(profile, contact)
    if operation == 'ideas':
        return {
            'ideas': list(
                Idea.objects.filter(hackathon=h, state='open')
                .exclude(author_id__in=excluded)
                .values('id', 'title', 'problem', 'audience', 'solution', 'mode', 'version', 'author_id')
            )
        }
    if operation == 'progress':
        proposals = TeamProposal.objects.filter(hackathon=h, snapshot__members__has_key=str(p.pk)).select_related(
            'idea', 'creator', 'target_team'
        )
        proposals = proposals.prefetch_related('consents')
        intents = list(
            MatchIntent.objects.filter(hackathon=h).filter(Q(sender=p) | Q(recipient=p)).order_by('-created')
        )
        contacts = matching.contacts_for_intents(h, p, intents)
        return {
            'proposals': [
                {
                    'id': str(item.pk),
                    'state': effective_state(item),
                    'state_label': '已过期' if effective_state(item) == 'expired' else item.get_state_display(),
                    'proposal_hash': item.proposal_hash,
                    'snapshot': item.snapshot,
                    'expires_at': item.expires_at,
                    'decisions': [
                        {'participant_id': c.participant_id, 'decision': c.decision} for c in item.consents.all()
                    ],
                }
                for item in proposals
            ],
            'intents': [
                {
                    'id': str(i.pk),
                    'sender_id': str(i.sender_id),
                    'recipient_id': str(i.recipient_id),
                    'message': i.message,
                    'state': effective_state(i),
                    'state_label': '已过期' if effective_state(i) == 'expired' else i.get_state_display(),
                    'version': i.version,
                    'contacts': contacts.get(i.pk, {}),
                }
                for i in intents
            ],
        }
    if operation == 'unmatched':
        if not matching.is_organizer(actor, h):
            raise PermissionDenied
        return {
            'people': [
                {'id': str(r.participant_id), 'name': r.participant.name}
                for r in MatchingProfile.objects.filter(
                    hackathon=h, help_requested=True, opt_in=True, participant__team=None
                )
                .exclude(visibility='paused')
                .select_related('participant')
            ]
        }
    raise Http404


@event_view('login')
@require_http_methods(['GET', 'POST', 'PATCH'])
def api(request, operation):
    h = request.hackathon
    try:
        matching.require_enabled(h)
        if request.method == 'GET':
            return JsonResponse(read_payload(h, request.user, operation, request.GET))
        if request.method == 'PATCH' and operation not in {'profile', 'ideas'}:
            return JsonResponse({'error': 'METHOD_NOT_ALLOWED'}, status=405)
        try:
            body = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'INVALID_JSON'}, status=422)
        if not isinstance(body, dict):
            return JsonResponse({'error': 'INVALID_JSON'}, status=422)
        if operation == 'ideas':
            p = matching.own_person(h, request.user)
            idea_id = body.get('idea_id')
            if idea_id:
                ident = serializers.UUIDField()
                try:
                    idea_id = ident.run_validation(idea_id)
                except serializers.ValidationError:
                    return JsonResponse({'error': 'INVALID_IDEA_ID'}, status=422)
            idea = get_object_or_404(Idea, hackathon=h, pk=idea_id, author=p) if idea_id else None
            form = IdeaForm(body, instance=idea)
            if not form.is_valid():
                return JsonResponse({'error': 'INVALID_IDEA', 'fields': form.errors.get_json_data()}, status=422)

            def callback(locked):
                return matching.save_idea(locked, request.user, form, idea_id)
        elif operation == 'profile':
            form = ProfileForm(body, hackathon=h)
            if not form.is_valid():
                return JsonResponse({'error': 'INVALID_PROFILE', 'fields': form.errors.get_json_data()}, status=422)

            def callback(locked):
                return matching.save_profile(locked, request.user, form.cleaned_data)
        else:
            cls = INPUTS.get(operation)
            if cls is None:
                raise Http404
            serializer = cls(data=body)
            if not serializer.is_valid():
                return JsonResponse({'error': 'INVALID_REQUEST', 'fields': serializer.errors}, status=422)

            def callback(locked):
                return dispatch(locked, request.user, operation, serializer.validated_data)

        receipt = matching.execute(
            h, request.user, operation, request.headers.get('Idempotency-Key', ''), body, callback
        )
        return JsonResponse({**receipt.result, 'receipt_id': str(receipt.pk)}, status=receipt.status)
    except matching.MatchingError as error:
        return JsonResponse({'error': error.code, 'message': error.message}, status=error.status)


@event_view('login')
@require_GET
def receipt(request, receipt_id):
    item = get_object_or_404(MatchingReceipt, hackathon=request.hackathon, actor=request.user, pk=receipt_id)
    return JsonResponse(
        {'receipt_id': str(item.pk), 'operation': item.operation, 'result': item.result, 'status': item.status}
    )


def effective_state(item):
    if item.state in {'pending', 'awaiting_consent'} and item.expires_at <= timezone.now():
        return 'expired'
    return item.state


@event_view('login')
@require_http_methods(['GET', 'POST'])
def market(request):
    h = request.hackathon
    if not h.matching_enabled:
        return page(request, 'matching_disabled')
    if not request.participant:
        return redirect(url(h, 'register'))
    if request.method == 'POST':
        operation = request.POST.get('operation', '')
        if operation not in INPUTS:
            raise Http404
        serializer = INPUTS[operation](data=request.POST)
        if not serializer.is_valid():
            messages.error(request, '请检查提交内容。')
        else:
            body = {
                k: request.POST.get(k)
                for k in sorted(request.POST)
                if k not in {'csrfmiddlewaretoken', 'idempotency_key'}
            }
            try:
                item = matching.execute(
                    h,
                    request.user,
                    operation,
                    request.POST.get('idempotency_key', ''),
                    body,
                    lambda locked: dispatch(locked, request.user, operation, serializer.validated_data),
                )
                if item.status >= 400 or 'error' in item.result:
                    messages.error(request, item.result.get('message', item.result.get('error', '操作失败。')))
                else:
                    messages.success(request, '已记录。接受交流不代表正式入队；正式名单需全员确认。')
            except matching.MatchingError as error:
                messages.error(request, error.message)
        return redirect(url(h, 'matching'))
    p = request.participant
    people = read_payload(h, request.user, 'people', request.GET)['people']
    teams = read_payload(h, request.user, 'teams', {})['teams']
    progress = read_payload(h, request.user, 'progress', {})
    proposed = []
    all_ids = {pid for item in progress['proposals'] for pid in item['snapshot']['members']}
    member_names = {str(person.pk): person.name for person in Participant.objects.filter(hackathon=h, pk__in=all_ids)}
    for item in progress['proposals']:
        ids = list(item['snapshot']['members'])
        item['names'] = [member_names[pid] for pid in ids if pid in member_names]
        item['can_decide'] = item['state'] == 'awaiting_consent' and all(
            str(c['participant_id']) != str(p.pk) for c in item['decisions']
        )
        proposed.append(item)
    ideas = list(
        Idea.objects.filter(hackathon=h, state='open')
        .exclude(author_id__in=matching.excluded_ids(h, p))
        .select_related('author')
    )
    contactable = set(
        MatchingProfile.objects.filter(hackathon=h, opt_in=True)
        .exclude(visibility='paused')
        .values_list('participant_id', flat=True)
    ) - matching.excluded_ids(h, p)
    for idea in ideas:
        idea.can_contact = idea.author_id in contactable and idea.author_id != p.pk
    if request.GET.get('saved') == '1':
        favorites = MatchingFeedback.objects.filter(hackathon=h, participant=p, decision='save')
        saved = {(f.target_type, str(f.target_id)) for f in favorites}
        people = [person for person in people if ('people', person['id']) in saved]
        teams = [team for team in teams if ('teams', team['id']) in saved]
        ideas = [idea for idea in ideas if ('ideas', str(idea.pk)) in saved]
    return page(
        request,
        'matching',
        own_members=Participant.objects.filter(hackathon=h, team_id=p.team_id) if p.team_id else [],
        people=people,
        market_teams=teams,
        ideas=ideas,
        progress=progress,
        proposals=proposed,
        recommendations=matching.recommendations(h, p, request.GET.get('target', 'people')),
        receipts=MatchingReceipt.objects.filter(hackathon=h, actor=request.user).order_by('-created')[:10],
        request_key=uuid.uuid4().hex,
    )


def submit_form(request, form, operation, body, callback):
    try:
        receipt = matching.execute(
            request.hackathon, request.user, operation, request.POST.get('idempotency_key', ''), body, callback
        )
    except matching.MatchingError as error:
        form.add_error(None, error.message)
        return None
    if receipt.status >= 400:
        form.add_error(None, receipt.result['message'])
        return None
    return receipt


@event_view('login')
@require_http_methods(['GET', 'POST'])
def profile(request):
    h = request.hackathon
    if not h.matching_enabled:
        raise Http404
    if not request.participant:
        return redirect(url(h, 'register'))
    p = request.participant
    current = matching.profile_for(p)
    contact = MatchingContact.objects.filter(hackathon=h, participant=p).first()
    form = ProfileForm(
        request.POST if request.method == 'POST' else None, initial=profile_initial(current, contact), hackathon=h
    )
    if current is None and p.unavailable_slots:
        form.initial['unavailable_slots'] = p.unavailable_slots
        form.initial['availability_confirmed'] = True
    if request.method == 'POST' and form.is_valid():
        body = {
            key: request.POST.getlist(key)
            for key in sorted(request.POST)
            if key not in {'csrfmiddlewaretoken', 'idempotency_key'}
        }
        receipt = submit_form(
            request,
            form,
            'profile',
            body,
            lambda locked: matching.save_profile(locked, request.user, form.cleaned_data),
        )
        if receipt:
            messages.success(request, '匹配资料已更新。')
            return redirect(url(h, 'matching'))
    return page(
        request,
        'matching_form',
        form=form,
        title='我能贡献什么，想寻找什么',
        subtitle='联系方式单独授权；愿意学习不会被计为已有技能。暂停匹配可选择单人继续探索。',
        request_key=uuid.uuid4().hex,
    )


@event_view('login')
@require_http_methods(['GET', 'POST'])
def idea_edit(request, idea_id=None):
    h = request.hackathon
    if not h.matching_enabled:
        raise Http404
    p = matching.own_person(h, request.user)
    item = get_object_or_404(Idea, hackathon=h, pk=idea_id, author=p) if idea_id else None
    initial = (
        {'version': item.version, 'interest_tags': ', '.join(item.interests), 'roles': list(item.role_weights)}
        if item
        else {}
    )
    form = IdeaForm(request.POST if request.method == 'POST' else None, instance=item, initial=initial)
    if request.method == 'POST' and form.is_valid():
        body = {
            key: request.POST.getlist(key)
            for key in sorted(request.POST)
            if key not in {'csrfmiddlewaretoken', 'idempotency_key'}
        }
        receipt = submit_form(
            request,
            form,
            f'idea:{idea_id or "new"}',
            body,
            lambda locked: matching.save_idea(locked, request.user, form, idea_id),
        )
        if receipt:
            return redirect(url(h, 'matching'))
    return page(
        request,
        'matching_form',
        form=form,
        title='发布 / 编辑 Idea',
        subtitle='组队型保留作者；揭榜型允许多个团队独立实现，作者不会自动占用名额。',
        request_key=uuid.uuid4().hex,
    )


@event_view('login')
@require_http_methods(['GET', 'POST'])
def proposal_create(request):
    h = request.hackathon
    if not h.matching_enabled:
        raise Http404
    p = matching.own_person(h, request.user)
    initial = {'member_ids': ', '.join(str(m.pk) for m in p.team.members.all()) if p.team_id else str(p.pk)}
    form = ProposalForm(request.POST if request.method == 'POST' else None, hackathon=h, initial=initial)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        body = {
            k: request.POST.get(k) for k in sorted(request.POST) if k not in {'csrfmiddlewaretoken', 'idempotency_key'}
        }
        receipt = submit_form(
            request,
            form,
            'proposals',
            body,
            lambda locked: matching.create_proposal(
                locked,
                request.user,
                [value.strip() for value in data['member_ids'].replace('，', ',').split(',')],
                data['idea'].pk,
                data['track'].pk,
                data['team_name'],
                data['agreement'],
            ),
        )
        if receipt:
            return redirect(url(h, 'matching'))
    return page(
        request,
        'matching_form',
        form=form,
        title='确认一份具体名单',
        subtitle='包括现成员和新增成员；每个人均须亲自确认本次名单与合作约定。',
        request_key=uuid.uuid4().hex,
    )


@event_view('organizer')
@require_http_methods(['GET', 'POST'])
def operations(request):
    h = request.hackathon
    if request.method == 'POST':
        operation = request.POST.get('operation')
        cls = INPUTS.get(operation)
        if operation not in {'configure', 'admission', 'matching-runs', 'freeze'} or cls is None:
            raise Http404
        serializer = cls(data=request.POST)
        if not serializer.is_valid():
            messages.error(request, '请填写合法的政策参数。')
        else:
            body = {
                k: request.POST.get(k)
                for k in sorted(request.POST)
                if k not in {'csrfmiddlewaretoken', 'idempotency_key'}
            }
            try:
                receipt = matching.execute(
                    h,
                    request.user,
                    operation,
                    request.POST.get('idempotency_key', ''),
                    body,
                    lambda locked: dispatch(locked, request.user, operation, serializer.validated_data),
                )
                if receipt.status < 400:
                    messages.success(request, '操作已记录，旧提案须按当前政策重新确认。')
                else:
                    messages.error(request, receipt.result['message'])
            except matching.MatchingError as error:
                messages.error(request, error.message)
        return redirect(url(h, 'matching-operations'))
    return page(
        request,
        'matching_operations',
        request_key=uuid.uuid4().hex,
        help_queue=MatchingProfile.objects.filter(hackathon=h, help_requested=True, opt_in=True, participant__team=None)
        .exclude(visibility='paused')
        .select_related('participant'),
        pending=MatchIntent.objects.filter(hackathon=h, state='pending', expires_at__lte=timezone.now()).select_related(
            'sender', 'recipient'
        ),
        runs=RecommendationRun.objects.filter(hackathon=h, viewer=None).order_by('-created')[:10],
        candidates=Participant.objects.filter(hackathon=h).select_related('team').order_by('name'),
    )
