import hashlib
import io
import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from eventyay.base.models import User
from eventyay.hackathon import identity
from eventyay.hackathon.account_models import AccountActionToken, AccountSecurity, AdmissionGrant, UpdateDelivery
from eventyay.hackathon.models import Hackathon, StudentAccount
from tests.hackathon.test_workflows import endpoint


pytestmark = pytest.mark.django_db
PASSWORD = 'SafeAccount!2026'


@pytest.fixture(autouse=True)
def account_cache(settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()


@pytest.fixture
def screened(hackathon):
    hackathon.admission_required = True
    hackathon.save(update_fields=['admission_required'])
    return hackathon


def verified(user, **preferences):
    return AccountSecurity.objects.create(
        user=user, verified_email=user.email, verified_at=timezone.now(), **preferences
    )


def permit(user, h, student_id='20260001', **changes):
    StudentAccount.objects.get_or_create(user=user, defaults={'student_id': student_id})
    return AdmissionGrant.objects.create(hackathon=h, student_id=student_id, email=user.email, **changes)


def token_from_mail():
    link = re.search(r'https?://[^\s]+', mail.outbox[-1].body).group()
    return parse_qs(urlsplit(link).query)['token'][0]


@pytest.mark.parametrize('identifier,email', [('20260001', 'person@example.org'), ('person@example.org', '')])
def test_signup_paths_create_one_account_and_require_email_verification(client, screened, identifier, email):
    response = client.post(
        endpoint(screened, 'student-signup'),
        {
            'identifier': identifier,
            'email': email,
            'name': '测试选手',
            'password': PASSWORD,
            'password_confirm': PASSWORD,
            'consent': 'on',
            'future_updates': 'on',
        },
    )
    assert response.status_code == 302
    user = User.objects.get(email='person@example.org')
    assert not identity.email_verified(user)
    assert user.hackathon_security.future_updates and not user.hackathon_security.activity_updates
    token = token_from_mail()
    assert AccountActionToken.objects.get().digest == hashlib.sha256(token.encode()).hexdigest()
    verify = endpoint(screened, 'verify-email')
    assert client.get(verify, {'token': token}).status_code == 200
    assert not identity.email_verified(user)  # A mail scanner's GET must not consume it.
    assert client.post(verify, {'token': token}).status_code == 302
    assert identity.email_verified(user)
    assert not identity.verify_token(token)
    landing = client.get(endpoint(screened, 'home'))
    assert landing.status_code == 200 and '账号已登录' in landing.content.decode()
    assert client.get(endpoint(screened, 'project')).status_code == 302


@pytest.mark.parametrize('identifier', ['20260001', 'CREATOR@EXAMPLE.ORG'])
def test_identifiers_share_account_and_cannot_follow_old_next_into_workspace(client, screened, user, identifier):
    user.set_password(PASSWORD)
    user.save()
    StudentAccount.objects.create(user=user, student_id='20260001')
    response = client.post(
        endpoint(screened, 'student-login') + '?next=' + endpoint(screened, 'project'),
        {'identifier': identifier, 'password': PASSWORD},
    )
    assert response.status_code == 302 and response.url == endpoint(screened, 'home')
    assert str(client.session['_auth_user_id']) == str(user.pk)


@pytest.mark.parametrize('name', ['teams', 'project', 'resources', 'schedule', 'gallery', 'stage-state'])
@pytest.mark.parametrize('method', ['get', 'head'])
def test_all_workspace_get_and_head_routes_are_closed(client, screened, user, participant, name, method):
    participant.admission_state = 'admitted'
    participant.save()
    verified(user)
    StudentAccount.objects.create(user=user, student_id='20260001')
    client.force_login(user)
    response = getattr(client, method)(endpoint(screened, name))
    assert response.status_code == 302
    assert response.url == endpoint(screened, 'home') + '#join'


def test_download_upload_and_api_are_closed_even_with_old_ids(client, screened, user):
    client.force_login(user)
    root = endpoint(screened, 'home')
    assert client.get(root + 'assets/00000000-0000-0000-0000-000000000001/').status_code == 302
    for path in ['project/uploads/', 'market/api/proposals/', 'teams/action/']:
        response = client.post(root + path, {})
        assert response.status_code == 403
        assert response.json()['error'] == 'ADMISSION_REQUIRED'


def test_list_requires_exact_verified_owner_and_revocation_affects_existing_session(client, screened, user):
    profile = verified(user)
    grant = permit(user, screened)
    client.force_login(user)
    assert identity.admitted(user, screened)
    assert client.get(endpoint(screened, 'resources')).status_code == 200
    grant.email = 'someone-else@example.org'
    grant.save()
    assert not identity.admitted(user, screened)
    grant.email = user.email
    grant.save()
    grant.approved = False
    grant.save()
    assert client.get(endpoint(screened, 'resources')).status_code == 302
    grant.approved = True
    grant.save()
    user.email = 'changed@example.org'
    user.save()
    assert not identity.admitted(user, screened)
    profile.refresh_from_db()
    assert profile.verified_email != user.email


def test_other_event_list_cannot_grant_access(screened, user, event):
    verified(user)
    permit(user, screened)
    other = Hackathon(id=999999, slug='other', event_id=event.pk, admission_required=True)
    assert not identity.admitted(user, other)


def test_manual_identity_assignment_still_requires_correct_student_and_verified_email(screened, user):
    grant = permit(user, screened, assigned_user=user)
    assert not identity.admitted(user, screened)
    verified(user)
    assert identity.admitted(user, screened)
    grant.student_id = '20260002'
    grant.save()
    assert not identity.admitted(user, screened)


def test_email_signup_can_bind_student_but_cannot_change_or_steal_binding(client, screened, user):
    verified(user)
    client.force_login(user)
    location = endpoint(screened, 'account')
    assert client.post(location, {'operation': 'bind-student', 'student_id': 'ab20260001'}).status_code == 302
    assert StudentAccount.objects.get(user=user).student_id == 'AB20260001'
    response = client.post(location, {'operation': 'bind-student', 'student_id': '20269999'})
    assert response.status_code == 200
    assert StudentAccount.objects.get(user=user).student_id == 'AB20260001'


def test_recovery_by_email_is_one_use_and_invalidates_sessions(client, screened, user):
    verified(user)
    user.set_password(PASSWORD)
    user.save()
    second = Client()
    second.force_login(user)
    location = endpoint(screened, 'student-password-reset-request')
    assert client.post(location, {'identifier': user.email}).status_code == 302
    token = token_from_mail()
    confirm = endpoint(screened, 'student-password-reset-confirm')
    assert (
        client.post(
            confirm, {'token': token, 'password': 'NewSafe!Password2026', 'password_confirm': 'NewSafe!Password2026'}
        ).status_code
        == 302
    )
    user.refresh_from_db()
    assert user.check_password('NewSafe!Password2026')
    assert client.post(confirm, {'token': token, 'password': PASSWORD, 'password_confirm': PASSWORD}).status_code == 200
    user.refresh_from_db()
    assert user.check_password('NewSafe!Password2026')
    second.get(endpoint(screened, 'home'))
    assert '_auth_user_id' not in second.session


def test_unverified_and_missing_recovery_are_indistinguishable(client, screened, user):
    location = endpoint(screened, 'student-password-reset-request')
    first = client.post(location, {'identifier': user.email}, follow=True)
    cache.clear()
    second = client.post(location, {'identifier': 'absent@example.org'}, follow=True)
    assert [str(m) for m in first.context['messages']] == [str(m) for m in second.context['messages']]
    assert len(mail.outbox) == 0


def test_mail_failure_can_retry_and_expired_wrong_purpose_tokens_fail(client, screened, user):
    import smtplib

    verified(user)
    with patch('eventyay.hackathon.identity.send_mail', side_effect=smtplib.SMTPException):
        assert not identity.send_action(user, screened, 'reset')
    assert identity.send_action(user, screened, 'reset')
    token = token_from_mail()
    assert not identity.verify_token(token)
    AccountActionToken.objects.filter(used_at__isnull=True).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert (
        '链接已失效'
        in client.get(endpoint(screened, 'student-password-reset-confirm'), {'token': token}).content.decode()
    )


def test_new_email_is_not_active_until_verified(client, screened, user):
    verified(user)
    user.set_password(PASSWORD)
    user.save()
    client.force_login(user)
    assert (
        client.post(
            endpoint(screened, 'account'),
            {'operation': 'change-email', 'email': 'new@example.org', 'password': PASSWORD},
        ).status_code
        == 302
    )
    user.refresh_from_db()
    assert user.email == 'creator@example.org'
    assert identity.email_verified(user)
    assert identity.verify_token(token_from_mail())
    user.refresh_from_db()
    assert user.email == 'new@example.org' and identity.email_verified(user)


def test_only_organizer_can_preview_apply_and_revoke_list(client, screened, organizer_user, user):
    location = endpoint(screened, 'admission-manage')
    client.force_login(user)
    assert client.get(location).status_code == 302
    client.force_login(organizer_user)
    csv = SimpleUploadedFile('list.csv', b'student_id,email,approved\n20260001,creator@example.org,true\n')
    assert client.post(location, {'operation': 'preview', 'file': csv}).status_code == 200
    assert not AdmissionGrant.objects.exists()
    preview = client.session['admission_import']
    assert client.post(location, {'operation': 'apply', 'preview_token': preview['token']}).status_code == 302
    assert AdmissionGrant.objects.get().approved
    assert client.post(location, {'operation': 'apply', 'preview_token': preview['token']}).status_code == 400
    assert (
        client.post(
            location, {'operation': 'grant', 'student_id': '20260001', 'email': user.email, 'reason': '更正名单'}
        ).status_code
        == 302
    )
    assert not AdmissionGrant.objects.get().approved


def test_non_admitted_opt_in_receives_updates_with_unsubscribe_and_delivery_dedup(client, screened, user, tmp_path):
    profile = verified(user, future_updates=True)
    body = tmp_path / 'message.txt'
    body.write_text('下一次活动通知', encoding='utf-8')
    args = [
        '--hackathon',
        screened.slug,
        '--audience',
        'future',
        '--campaign',
        'test-20261113',
        '--subject',
        '后续活动',
        '--body-file',
        str(body),
    ]
    call_command('send_hackathon_update', *args, stdout=io.StringIO())
    assert len(mail.outbox) == 0 and not identity.admitted(user, screened)
    call_command('send_hackathon_update', *args, '--send', stdout=io.StringIO())
    assert len(mail.outbox) == 1 and UpdateDelivery.objects.get().sent_at
    call_command('send_hackathon_update', *args, '--send', stdout=io.StringIO())
    assert len(mail.outbox) == 1
    token = token_from_mail()
    location = endpoint(screened, 'unsubscribe')
    assert client.get(location, {'token': token}).status_code == 200
    profile.refresh_from_db()
    assert profile.future_updates
    assert client.post(location, {'token': token}).status_code == 200
    profile.refresh_from_db()
    assert not profile.future_updates


def test_staff_can_still_manage_with_empty_list(client, screened, organizer_user):
    client.force_login(organizer_user)
    assert client.get(endpoint(screened, 'manage')).status_code == 200
    assert client.get(endpoint(screened, 'admission-manage')).status_code == 200


def test_password_recovery_cancels_pending_email_takeover_and_legacy_recovery(client, screened, user):
    profile = verified(user)
    profile.pending_email = 'attacker@example.org'
    profile.save()
    user.pw_reset_token = 'legacy-recovery-token'
    user.pw_reset_time = timezone.now()
    user.save()
    assert identity.send_action(user, screened, 'verify')
    stolen_verify = token_from_mail()
    assert identity.send_action(user, screened, 'reset')
    reset = token_from_mail()
    assert (
        client.post(
            endpoint(screened, 'student-password-reset-confirm'),
            {
                'token': reset,
                'password': PASSWORD,
                'password_confirm': PASSWORD,
            },
        ).status_code
        == 302
    )
    user.refresh_from_db()
    profile.refresh_from_db()
    assert not user.pw_reset_token and not user.pw_reset_time and not profile.pending_email
    assert not identity.verify_token(stolen_verify)
    assert user.email == 'creator@example.org'
    assert not AccountActionToken.objects.filter(used_at__isnull=True).exists()


def test_other_password_change_invalidates_unconsumed_verification_token(screened, user):
    assert identity.send_action(user, screened, 'verify')
    raw = token_from_mail()
    user.set_password(PASSWORD)
    user.save()
    assert not identity.verify_token(raw)


def test_unsubscribe_still_works_when_activity_becomes_private(client, screened, user):
    from django.urls import reverse

    profile = verified(user, activity_updates=True, future_updates=True)
    token = identity.unsubscribe_token(profile)
    screened.is_public = False
    screened.save()
    location = reverse('hackathon:unsubscribe-account')
    response = client.get(location, {'token': token})
    assert response.status_code == 200
    assert screened.name not in response.content.decode()
    assert client.post(location, {'token': token}).status_code == 200
    profile.refresh_from_db()
    assert not profile.activity_updates and not profile.future_updates


def test_bad_csv_is_atomic_and_cannot_reuse_another_sessions_preview(client, screened, organizer_user):
    location = endpoint(screened, 'admission-manage')
    client.force_login(organizer_user)
    csv = SimpleUploadedFile('list.csv', b'student_id,email\n20260001,a@example.org\n20260001,b@example.org\n')
    response = client.post(location, {'operation': 'preview', 'file': csv})
    assert response.status_code == 200 and '重复' in response.content.decode()
    assert not AdmissionGrant.objects.exists()
    assert client.post(location, {'operation': 'apply', 'preview_token': 'copied-token'}).status_code == 400
