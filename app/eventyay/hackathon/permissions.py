from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django_scopes import scope

from eventyay.base.models import Team as OrganizerTeam
from eventyay.hackathon.models import Hackathon, Participant, StaffAssignment


def is_organizer(user, hackathon):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.is_administrator:
        return True
    return (
        OrganizerTeam.objects.filter(
            organizer=hackathon.event.organizer,
            members=user,
            can_change_event_settings=True,
        )
        .filter(Q(all_events=True) | Q(limit_events=hackathon.event))
        .exists()
    )


def event_view(access='public'):
    def decorator(view):
        @wraps(view)
        def wrapped(request, hackathon_slug, *args, **kwargs):
            hackathon = get_object_or_404(Hackathon.objects.select_related('event__organizer'), slug=hackathon_slug)
            with scope(event=hackathon.event), timezone.override(hackathon.event.timezone):
                request.hackathon = hackathon
                request.hackathon_organizer = is_organizer(request.user, hackathon)
                request.hackathon_roles = set()
                request.participant = None
                if request.user.is_authenticated:
                    request.hackathon_roles = set(
                        StaffAssignment.objects.filter(hackathon=hackathon, user=request.user).values_list(
                            'role', flat=True
                        )
                    )
                    request.participant = (
                        Participant.objects.filter(hackathon=hackathon, user=request.user)
                        .select_related('team__track')
                        .first()
                    )
                if not hackathon.is_public and not request.hackathon_organizer and not request.hackathon_roles:
                    raise Http404
                if access != 'public' and not request.user.is_authenticated:
                    login_name = {'organizer': 'organizer-login', 'judge': 'judge-login'}.get(access, 'student-login')
                    return redirect_to_login(
                        request.get_full_path(),
                        login_url=reverse(
                            f'hackathon:{login_name}',
                            kwargs={'hackathon_slug': hackathon.slug},
                        ),
                    )
                allowed = {
                    'public': True,
                    'login': True,
                    'organizer': request.hackathon_organizer,
                    'judge': 'judge' in request.hackathon_roles,
                    'volunteer': request.hackathon_organizer or 'volunteer' in request.hackathon_roles,
                    'staff': request.hackathon_organizer or bool(request.hackathon_roles),
                }
                if not allowed.get(access, False):
                    raise PermissionDenied
                response = view(request, *args, **kwargs)
                response['Cache-Control'] = 'private, no-store'
                return response

        return wrapped

    return decorator
