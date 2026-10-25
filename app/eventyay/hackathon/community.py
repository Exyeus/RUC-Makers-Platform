from django.db.models import Q

from eventyay.hackathon import matching
from eventyay.hackathon.models import CommunityPost, Idea


def square_context(hackathon, participant, params) -> dict:
    posts = CommunityPost.objects.filter(hackathon=hackathon, is_public=True)
    ideas = (
        Idea.objects.filter(hackathon=hackathon, state='open')
        if participant and hackathon.matching_enabled
        else Idea.objects.none()
    )
    if participant:
        excluded = matching.excluded_ids(hackathon, participant)
        posts = posts.exclude(author_id__in=excluded)
        ideas = ideas.exclude(author_id__in=excluded)
    pool_posts = posts.filter(kind__in=['showcase', 'idea', 'co_create']).select_related('author')
    kind = params.get('kind', '')
    if kind in CommunityPost.Kind.values:
        posts = posts.filter(kind=kind)
    query = params.get('q', '').strip()
    if query:
        posts = posts.filter(Q(title__icontains=query) | Q(body__icontains=query))
        ideas = ideas.filter(Q(title__icontains=query) | Q(problem__icontains=query))
    return {
        'community_posts': posts.select_related('author')[:60],
        'pool_ideas': ideas.select_related('author')[:60],
        'pool_posts': pool_posts[:60],
        'post_kinds': CommunityPost.Kind.choices,
        'selected_kind': kind,
    }
