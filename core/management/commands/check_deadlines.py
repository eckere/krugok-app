"""
Проверяет дедлайны задач и отправляет Telegram-уведомления.

Пример crontab для VPS:
*/15 * * * * cd /path/to/app && /path/to/venv/bin/python manage.py check_deadlines >> /var/log/krugok-deadlines.log 2>&1
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Notification, Task
from core.rate_limit import cleanup_rate_limits
from core.telegram_notifications import notify, process_outbound


class Command(BaseCommand):
    help = 'Проверяет дедлайны и отправляет уведомления в Telegram.'

    def handle(self, *args: object, **options: object) -> None:
        now = timezone.now()
        approaching_before = now + timedelta(hours=24)
        tasks = (
            Task.objects.filter(deadline__isnull=False)
            .exclude(status=Task.Status.DONE)
            .select_related('assignee', 'creator')
        )

        processed = 0

        for task in tasks.iterator():
            processed += 1
            kind = None
            if task.deadline < now:
                kind = Notification.Kind.DEADLINE_OVERDUE
            elif task.deadline <= approaching_before:
                kind = Notification.Kind.DEADLINE_APPROACHING

            if kind is None:
                continue

            notify(task, kind)

        sent, failed = process_outbound(limit=200)
        cleanup_rate_limits()
        self.stdout.write(
            f'Обработано: {processed}; отправлено: {sent}; ошибок: {failed}.'
        )
