# 工单改成逐条线程：先把旧的单条 reply 搬进线程，再撤掉那个会被覆盖的字段。
# 撤销 reply 列不可逆（旧内容已复制为一条发言，回滚后那一列会是空的），
# 因此部署顺序是：停旧实例 → migrate hackathon → 起新版本。

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def move_replies_into_thread(apps, schema_editor):
    SupportTicket = apps.get_model('hackathon', 'SupportTicket')
    TicketMessage = apps.get_model('hackathon', 'TicketMessage')
    for ticket in SupportTicket.objects.exclude(reply=''):
        message = TicketMessage.objects.create(
            hackathon_id=ticket.hackathon_id,
            ticket_id=ticket.pk,
            # 老数据只在工单上记了一个 handler：有 handler 才算组委会发言，没有就退回开单人本人。
            author_id=ticket.handler_id or ticket.user_id,
            from_staff=ticket.handler_id is not None,
            body=ticket.reply,
        )
        # 旧数据没有逐条回复的写入时间，只能挂到工单最后修改时间上。
        TicketMessage.objects.filter(pk=message.pk).update(created=ticket.updated)


class Migration(migrations.Migration):

    dependencies = [
        ('hackathon', '0015_content_publish_state'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TicketMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('updated', models.DateTimeField(auto_now=True, null=True)),
                ('from_staff', models.BooleanField(default=False, verbose_name='由工作人员发出')),
                ('body', models.TextField(max_length=5000, verbose_name='内容')),
                (
                    'author',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'hackathon',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to='hackathon.hackathon',
                    ),
                ),
                (
                    'ticket',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='messages',
                        to='hackathon.supportticket',
                    ),
                ),
            ],
            options={
                'ordering': ['created', 'pk'],
            },
        ),
        migrations.RunPython(move_replies_into_thread, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='supportticket',
            name='state',
            field=models.CharField(
                choices=[
                    ('open', '待主办处理'),
                    ('doing', '处理中'),
                    ('waiting_user', '待选手补充'),
                    ('resolved', '已解决'),
                ],
                default='open',
                help_text='待选手补充会在对方参赛总览持续提示；作品已提交时成为下一步。',
                max_length=16,
                verbose_name='状态',
            ),
        ),
        migrations.RemoveField(
            model_name='supportticket',
            name='reply',
        ),
    ]
