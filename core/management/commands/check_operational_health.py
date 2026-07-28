import sqlite3
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import OutboundMessage


class Command(BaseCommand):
    help = 'Проверяет БД и критичные очереди; ненулевой код означает проблему.'

    def handle(self, *args, **options):
        database_path = Path(settings.DATABASES['default']['NAME'])
        connection = sqlite3.connect(
            f'file:{database_path.resolve()}?mode=ro',
            uri=True,
        )
        try:
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            connection.close()
        if integrity != 'ok':
            raise CommandError(f'SQLite integrity_check: {integrity}')

        exhausted = OutboundMessage.objects.filter(
            status=OutboundMessage.Status.FAILED,
            attempt_count__gte=settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS,
        ).count()
        stuck = OutboundMessage.objects.filter(
            status=OutboundMessage.Status.SENDING,
            last_attempt_at__lt=timezone.now() - timedelta(minutes=10),
        ).count()
        if exhausted or stuck:
            raise CommandError(
                f'Проблемы очереди: исчерпано={exhausted}, зависло={stuck}.'
            )
        self.stdout.write(self.style.SUCCESS('Operational health: ok.'))
