"""Preserve the legacy workspace column while allowing new activities to save."""

from django.db import migrations


def set_legacy_default(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT 1 FROM information_schema.columns '
            "WHERE table_schema=current_schema() AND table_name='hackathon_hackathon' "
            "AND column_name='is_default'"
        )
        if cursor.fetchone():
            cursor.execute('ALTER TABLE hackathon_hackathon ALTER COLUMN is_default SET DEFAULT FALSE')


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0017_score_partial_draft')]
    operations = [migrations.RunPython(set_legacy_default, reverse_code=migrations.RunPython.noop)]
