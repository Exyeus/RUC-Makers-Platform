from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0025_stage_order_requests')]
    operations = [
        migrations.AddField(
            model_name='uploadsession',
            name='transport',
            field=models.CharField(default='relay', db_default='relay', max_length=12),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='storage_name',
            field=models.CharField(blank=True, db_default='', max_length=512),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='storage_upload_id',
            field=models.CharField(blank=True, db_default='', max_length=512),
        ),
    ]
