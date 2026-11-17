"""Keep the legacy enable flag compatible with new activity creation."""

from django.db import migrations


def set_legacy_enabled_default(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT 1 FROM information_schema.columns '
            "WHERE table_schema=current_schema() AND table_name='hackathon_hackathon' "
            "AND column_name='is_enabled'"
        )
        if cursor.fetchone():
            cursor.execute('ALTER TABLE hackathon_hackathon ALTER COLUMN is_enabled SET DEFAULT TRUE')


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0018_legacy_workspace_default')]
    operations = [migrations.RunPython(set_legacy_enabled_default, reverse_code=migrations.RunPython.noop)]
