from django.db import migrations


def isolate_existing_hackathons(apps, schema_editor):
    # Workspaces created before the camp module belong to the existing hackathon business.
    hackathon = apps.get_model('hackathon', 'Hackathon')
    hackathon.objects.using(schema_editor.connection.alias).all().update(program='hackathon')


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0011_maker_camp_community')]
    operations = [migrations.RunPython(isolate_existing_hackathons, migrations.RunPython.noop)]
