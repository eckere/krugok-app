from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_production_readiness'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramuser',
            name='theme_preference',
            field=models.CharField(
                choices=[
                    ('telegram', 'Как в Telegram'),
                    ('light', 'Светлая'),
                    ('dark', 'Тёмная'),
                ],
                default='telegram',
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name='outboundmessage',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Ожидает'),
                    ('sending', 'Отправляется'),
                    ('sent', 'Отправлено'),
                    ('failed', 'Ошибка'),
                    ('cancelled', 'Закрыто вручную'),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
    ]
