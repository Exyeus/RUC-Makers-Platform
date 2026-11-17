import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0032_judging_workflow'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            model_name='uploadsession',
            name='intent_manifest',
            field=models.JSONField(
                blank=True, default=dict, db_default={}, verbose_name='上传登记时的作品信息与已有材料'
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='final_material_kind',
            field=models.CharField(
                blank=True,
                db_default='',
                choices=[('slides', 'PDF 演示文稿'), ('video', '现场视频')],
                max_length=12,
                verbose_name='现场上场材料（手动选择）',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='media_probe',
            field=models.JSONField(blank=True, default=dict, db_default={}, verbose_name='视频校验结果'),
        ),
        migrations.AlterField(
            model_name='asset',
            name='kind',
            field=models.CharField(
                choices=[
                    ('poster', '项目海报'),
                    ('slides', '演示文稿'),
                    ('video', '备用录屏'),
                    ('source', '项目附件'),
                    ('preliminary', '初评短视频'),
                ],
                max_length=12,
                verbose_name='材料类型',
            ),
        ),
        migrations.AlterField(
            model_name='uploadsession',
            name='kind',
            field=models.CharField(
                choices=[
                    ('poster', '项目海报'),
                    ('slides', '演示文稿'),
                    ('video', '备用录屏'),
                    ('source', '项目附件'),
                    ('preliminary', '初评短视频'),
                ],
                max_length=12,
            ),
        ),
        migrations.CreateModel(
            name='MaterialPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('updated', models.DateTimeField(auto_now=True, null=True)),
                ('enabled', models.BooleanField(default=True, verbose_name='启用初评与上场材料要求')),
                (
                    'require_preliminary_video',
                    models.BooleanField(default=True, verbose_name='正式提交必须有初评短视频'),
                ),
                (
                    'preliminary_max_seconds',
                    models.PositiveIntegerField(
                        blank=True, default=60, null=True, verbose_name='初评视频最长秒数（留空表示尚未定案）'
                    ),
                ),
                (
                    'require_final_material',
                    models.BooleanField(default=True, verbose_name='正式提交必须选择 PDF 或现场视频'),
                ),
                ('revision', models.PositiveIntegerField(default=1)),
                ('hackathon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='hackathon.hackathon')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('hackathon',), name='hack_material_policy')]},
        ),
        migrations.CreateModel(
            name='SubmissionSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('updated', models.DateTimeField(auto_now=True, null=True)),
                ('version', models.PositiveIntegerField()),
                ('captured_at', models.DateTimeField()),
                ('manifest', models.JSONField()),
                ('sha256', models.CharField(max_length=64)),
                ('reason', models.CharField(max_length=500)),
                (
                    'captured_by',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
                ),
                ('hackathon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='hackathon.hackathon')),
            ],
            options={
                'ordering': ['-version'],
                'constraints': [
                    models.UniqueConstraint(fields=('hackathon', 'version'), name='hack_submission_snapshot')
                ],
            },
        ),
    ]
