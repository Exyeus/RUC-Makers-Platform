from django.db import migrations, models


def keep_only_newest_station_token(apps, schema_editor):
    """同一活动只保留最新的一枚令牌，为后面的唯一约束清场。"""
    StationToken = apps.get_model('hackathon', 'StationToken')
    for hackathon_id in StationToken.objects.values_list('hackathon_id', flat=True).distinct():
        rows = list(StationToken.objects.filter(hackathon_id=hackathon_id).order_by('-created', '-pk'))
        for row in rows[1:]:
            row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hackathon', '0018_alter_joinrequest_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='stationtoken',
            name='last_seen_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近一次同步时间'),
        ),
        migrations.RunPython(keep_only_newest_station_token, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='stationtoken',
            constraint=models.UniqueConstraint(fields=('hackathon',), name='hack_one_station_token'),
        ),
    ]
