from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from eventyay.hackathon.models import StudentAccount


class Command(BaseCommand):
    help = 'Reset a student account password after the organizer has verified their identity.'

    def add_arguments(self, parser):
        parser.add_argument('student_id', help='Student identifier; case insensitive.')

    def handle(self, student_id, **options):
        try:
            account = StudentAccount.objects.select_related('user').get(student_id=student_id.strip().upper())
        except StudentAccount.DoesNotExist as error:
            raise CommandError('学号未注册。') from error
        try:
            password = getpass('新密码（至少 10 位，不回显）：')
            confirmed = getpass('再次输入新密码：')
        except (EOFError, KeyboardInterrupt) as error:
            raise CommandError('已取消密码重置。') from error
        if password != confirmed:
            raise CommandError('两次密码不一致。')
        if len(password) < 10 or len(password) > 128 or password.isdigit() or password.upper() == account.student_id:
            raise CommandError('密码必须为 10–128 位，不能是纯数字或与学号相同。')
        try:
            validate_password(password, account.user)
        except ValidationError as error:
            raise CommandError(' '.join(error.messages)) from error
        with transaction.atomic():
            account.user.set_password(password)
            account.user.update_session_token()
            account.user.save(update_fields=['password'])
        self.stdout.write(self.style.SUCCESS('密码已更新，原有登录会话已失效。'))
