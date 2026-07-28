import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def separate_telegram_usernames(apps, schema_editor):
    TelegramUser = apps.get_model('core', 'TelegramUser')
    database = schema_editor.connection.alias
    for user in TelegramUser.objects.using(database).exclude(
        telegram_id__isnull=True
    ).iterator():
        previous_username = user.username
        user.telegram_username = previous_username
        user.username = f'tg_{user.telegram_id}'
        user.save(update_fields=['telegram_username', 'username'])


def restore_telegram_usernames(apps, schema_editor):
    TelegramUser = apps.get_model('core', 'TelegramUser')
    database = schema_editor.connection.alias
    for user in TelegramUser.objects.using(database).exclude(
        telegram_username=''
    ).iterator():
        if not TelegramUser.objects.using(database).exclude(pk=user.pk).filter(
            username=user.telegram_username
        ).exists():
            user.username = user.telegram_username
            user.save(update_fields=['username'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_notification_retries'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='edited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='anonymized_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='notify_comments',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='notify_assignments',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='notify_deadlines',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='notify_messages',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='telegram_username',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='timezone',
            field=models.CharField(default='Europe/Moscow', max_length=64),
        ),
        migrations.RunPython(
            separate_telegram_usernames,
            restore_telegram_usernames,
        ),
        migrations.AddField(
            model_name='invitecode',
            name='project',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='invite_codes',
                to='core.project',
            ),
        ),
        migrations.AddField(
            model_name='invitecode',
            name='project_role',
            field=models.CharField(
                choices=[('admin', 'Админ'), ('member', 'Участник')],
                default='member',
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name='stage',
            name='unique_stage_order_per_project',
        ),
        migrations.AddConstraint(
            model_name='stage',
            constraint=models.UniqueConstraint(
                condition=Q(is_archived=False, order__isnull=False),
                fields=('project', 'order'),
                name='unique_active_stage_order_per_project',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(
                choices=[
                    ('deadline_set', 'Дедлайн установлен'),
                    ('deadline_approaching', 'Дедлайн приближается'),
                    ('deadline_overdue', 'Дедлайн просрочен'),
                    ('task_assigned', 'Назначена задача'),
                    ('comment_added', 'Добавлен комментарий'),
                    ('message_added', 'Новое сообщение'),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Ожидает отправки'),
                    ('sending', 'Отправляется'),
                    ('sent', 'Отправлено'),
                    ('failed', 'Ошибка отправки'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('action', models.CharField(db_index=True, max_length=64)),
                ('entity_type', models.CharField(db_index=True, max_length=64)),
                ('entity_id', models.CharField(blank=True, max_length=64)),
                ('entity_label', models.CharField(blank=True, max_length=255)),
                ('changes', models.JSONField(blank=True, default=dict)),
                ('ip_hash', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    'actor',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='audit_events',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(
                        fields=['entity_type', 'entity_id', '-created_at'],
                        name='core_auditl_entity__1c230b_idx',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='OutboundMessage',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('kind', models.CharField(max_length=32)),
                ('dedupe_key', models.CharField(max_length=128, unique=True)),
                ('text', models.TextField()),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('pending', 'Ожидает'),
                            ('sending', 'Отправляется'),
                            ('sent', 'Отправлено'),
                            ('failed', 'Ошибка'),
                        ],
                        db_index=True,
                        default='pending',
                        max_length=16,
                    ),
                ),
                ('attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                (
                    'next_retry_at',
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                (
                    'notification',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='outbound_message',
                        to='core.notification',
                    ),
                ),
                (
                    'recipient',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='outbound_messages',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['created_at'],
                'indexes': [
                    models.Index(
                        fields=['status', 'next_retry_at'],
                        name='core_outbou_status_151817_idx',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='RateLimitBucket',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('key_hash', models.CharField(max_length=64, unique=True)),
                ('window_started_at', models.DateTimeField()),
                ('request_count', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['updated_at'],
                        name='core_rateli_updated_2ef895_idx',
                    ),
                ],
            },
        ),
    ]
