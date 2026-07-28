"""
Проверяет дедлайны задач и отправляет Telegram-уведомления.

Пример crontab для VPS:
*/15 * * * * cd /path/to/app && /path/to/venv/bin/python manage.py check_deadlines >> /var/log/krugok-deadlines.log 2>&1
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from core.models import Notification, Task
from core.telegram_notifications import notify


class Command(BaseCommand):
    help = 'Проверяет дедлайны и отправляет уведомления в Telegram.'

    def handle(self, *args: object, **options: object) -> None:
        now = timezone.now()
        approaching_before = now + timedelta(hours=24)
        retryable_notifications = (
            Notification.objects.filter(
                status=Notification.Status.FAILED,
                attempt_count__lt=settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS,
                task__status__in=[
                    Task.Status.NEW,
                    Task.Status.IN_PROGRESS,
                ],
            )
            .filter(
                models.Q(next_retry_at__isnull=True)
                | models.Q(next_retry_at__lte=now)
            )
            .select_related('task__assignee', 'task__creator')
        )
        tasks = (
            Task.objects.filter(deadline__isnull=False)
            .exclude(status=Task.Status.DONE)
            .select_related('assignee', 'creator')
        )

        processed = 0
        sent = 0
        failed = 0

        for existing in retryable_notifications.iterator():
            previous_attempt_count = existing.attempt_count
            notification = notify(existing.task, existing.kind)
            if (
                notification is None
                or notification.attempt_count == previous_attempt_count
            ):
                continue
            if notification.status == Notification.Status.SENT:
                sent += 1
            elif notification.status == Notification.Status.FAILED:
                failed += 1

        for task in tasks.iterator():
            processed += 1
            kind = None
            if task.deadline < now:
                kind = Notification.Kind.DEADLINE_OVERDUE
            elif task.deadline <= approaching_before:
                kind = Notification.Kind.DEADLINE_APPROACHING

            if kind is None:
                continue

            previous = task.notifications.filter(kind=kind).first()
            previous_attempt_count = previous.attempt_count if previous else 0
            notification = notify(task, kind)
            if (
                notification is None
                or notification.attempt_count == previous_attempt_count
            ):
                continue
            if notification.status == Notification.Status.SENT:
                sent += 1
            elif notification.status == Notification.Status.FAILED:
                failed += 1

        self.stdout.write(
            f'Обработано: {processed}; отправлено: {sent}; ошибок: {failed}.'
        )
