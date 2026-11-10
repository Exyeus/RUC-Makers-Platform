from django.db import migrations, models


CHOICES = [('draft', '草稿'), ('published', '已发布'), ('archived', '已归档')]
PUBLISHABLE = ['announcement', 'resource', 'scheduleitem', 'track']


def publishable_fields(model_name):
    """Existing content keeps showing up: the column is stamped published, then new rows start as drafts."""
    return [
        migrations.AddField(
            model_name=model_name,
            name='state',
            field=models.CharField(
                choices=CHOICES, default='published', max_length=12, verbose_name='发布状态'
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name=model_name,
            name='published_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最后发布时间'),
        ),
        migrations.AlterField(
            model_name=model_name,
            name='state',
            field=models.CharField(choices=CHOICES, default='draft', max_length=12, verbose_name='发布状态'),
        ),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ('hackathon', '0013_demoslot_ended_at'),
    ]

    operations = [operation for name in PUBLISHABLE for operation in publishable_fields(name)]
