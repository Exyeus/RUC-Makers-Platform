from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from eventyay.hackathon.models import Hackathon


class Command(BaseCommand):
    help = 'Rename one existing workspace URL slug; leave its event and all associated records in place.'

    def add_arguments(self, parser):
        parser.add_argument('old_slug')
        parser.add_argument('new_slug')
        parser.add_argument(
            '--apply', action='store_true', help='Save the new slug; otherwise only preview the change.'
        )

    @transaction.atomic
    def handle(self, old_slug, new_slug, apply, **options):
        try:
            hackathon = Hackathon.objects.select_for_update().get(slug=old_slug)
        except Hackathon.DoesNotExist as error:
            raise CommandError('Source workspace not found.') from error
        try:
            Hackathon._meta.get_field('slug').clean(new_slug, hackathon)
        except ValidationError as error:
            raise CommandError('The new slug must be a valid workspace identifier.') from error
        if new_slug == old_slug:
            self.stdout.write('Slug unchanged.')
            return
        if new_slug == 'camp':
            raise CommandError('camp is reserved for legacy URL redirects; choose another workspace slug.')
        if Hackathon.objects.filter(slug=new_slug).exists():
            raise CommandError('The target slug already belongs to another workspace.')
        if apply:
            hackathon.slug = new_slug
            try:
                hackathon.save(update_fields=['slug'])
            except IntegrityError as error:
                raise CommandError('The target slug could not be saved; no changes were applied.') from error
        mode = 'Applied' if apply else 'Dry run'
        self.stdout.write(f'{mode}: /hackathon/{old_slug}/ -> /hackathon/{new_slug}/')
