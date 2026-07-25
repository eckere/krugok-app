"""
Проверяет дедлайны задач и отправляет Telegram-уведомления.

Пример crontab для VPS:
*/15 * * * * cd /path/to/app && /path/to/venv/bin/python manage.py check_deadlines >> /var/log/krugok-deadlines.log 2>&1
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Notification, Task
from core.telegram_notifications import notify


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
        sent = 0
        failed = 0

        for task in tasks.iterator():
            processed += 1
            kind = None
            if task.deadline < now:
                kind = Notification.Kind.DEADLINE_OVERDUE
            elif task.deadline <= approaching_before:
                kind = Notification.Kind.DEADLINE_APPROACHING

            if kind is None:
                continue

            already_exists = task.notifications.filter(kind=kind).exists()
            notification = notify(task, kind)
            if already_exists or notification is None:
                continue
            if notification.status == Notification.Status.SENT:
                sent += 1
            elif notification.status == Notification.Status.FAILED:
                failed += 1

        self.stdout.write(
            f'Обработано: {processed}; отправлено: {sent}; ошибок: {failed}.'
        )
