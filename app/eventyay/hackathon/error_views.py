from django.http import HttpResponse
from django.template.loader import get_template
from django.views import defaults
from django.views.csrf import csrf_failure as django_csrf_failure
from eventyay.base.views import errors as original
from eventyay.base.middleware import request_prefers_json_api


def is_workspace_page(request):
    return (request.path.startswith('/hackathon/tongzhou26/')
            and not request_prefers_json_api(request)
            and request.headers.get('X-Requested-With') != 'XMLHttpRequest'
            and ('text/html' in request.headers.get('Accept', '')
                 or not request.headers.get('Accept', '')))


def render_error(code, status):
    # No request context: error pages must render when database/session is down.
    return HttpResponse(get_template('hackathon/errors/' + code + '.html').render({}), status=status)


def bad_request(request, exception):
    return render_error('400', 400) if is_workspace_page(request) else defaults.bad_request(request, exception)


def permission_denied(request, exception):
    return render_error('403', 403) if is_workspace_page(request) else defaults.permission_denied(request, exception)


def not_found(request, exception):
    return render_error('404', 404) if is_workspace_page(request) else original.page_not_found(request, exception)


def server_error(request):
    return render_error('500', 500) if is_workspace_page(request) else original.server_error(request)


def csrf_failure(request, reason=''):
    return render_error('403_csrf', 403) if is_workspace_page(request) else django_csrf_failure(request, reason=reason)
