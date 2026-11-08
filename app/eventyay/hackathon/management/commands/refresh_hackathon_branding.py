from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_scopes import scope

from eventyay.base.models import Event
from eventyay.hackathon.models import Announcement, Hackathon, Resource
from eventyay.hackathon.seed import RESOURCES


OLD_NAME = '人大创客营'
NEW_NAME = '人大通州黑客松'
OLD_DESCRIPTION = '从一个真实问题出发，分享灵感、学习工具、完成小作品，在两天的创客营里找到一起探索的伙伴。'


class Command(BaseCommand):
    help = 'Refresh unchanged default UI branding in one workspace; preserve custom content and technical identifiers.'

    def add_arguments(self, parser):
        parser.add_argument('slug', help='Existing workspace slug.')
        parser.add_argument('--apply', action='store_true', help='Save changes; without this flag only report them.')

    @transaction.atomic
    def handle(self, slug, apply, **options):
        try:
            hackathon = Hackathon.objects.select_for_update().get(slug=slug)
        except Hackathon.DoesNotExist as error:
            raise CommandError('Workspace not found.') from error

        changes = 0
        for field, old, new in (
            ('name', OLD_NAME, NEW_NAME),
            ('description', OLD_DESCRIPTION, OLD_DESCRIPTION.replace('创客营', '黑客松')),
        ):
            changes += self.refresh_field(hackathon, field, old, new, apply)

        event = Event.objects.select_for_update().get(pk=hackathon.event_id)
        names = event.name.data
        if isinstance(names, dict):
            updated_names = {locale: NEW_NAME if name == OLD_NAME else name for locale, name in names.items()}
        else:
            updated_names = NEW_NAME if names == OLD_NAME else names
        if names != updated_names:
            self.stdout.write('Event.name')
            if apply:
                event.name = updated_names
                event.save(update_fields=['name'])
            changes += 1

        with scope(event=event):
            for announcement in Announcement.objects.select_for_update().filter(hackathon=hackathon):
                changes += self.refresh_field(
                    announcement, 'title', f'欢迎来到{OLD_NAME}工作台', f'欢迎来到{NEW_NAME}工作台', apply
                )
            titles = {
                resource['title'].replace('黑客松', '创客营'): resource['title']
                for resource in RESOURCES
                if '黑客松' in resource['title']
            }
            for resource in Resource.objects.select_for_update().filter(hackathon=hackathon, title__in=titles):
                changes += self.refresh_field(resource, 'title', resource.title, titles[resource.title], apply)

        mode = 'Applied' if apply else 'Dry run'
        self.stdout.write(f'{mode}: {changes} field(s).')

    def refresh_field(self, record, field, old, new, apply):
        if getattr(record, field) != old:
            return 0
        self.stdout.write(f'{record._meta.object_name}.{field}')
        if apply:
            setattr(record, field, new)
            record.save(update_fields=[field])
        return 1
