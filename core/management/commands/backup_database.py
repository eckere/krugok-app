import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Создаёт консистентный SQLite backup и проверяет его целостность.'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', default='/backups')
        parser.add_argument('--retain-days', type=int, default=14)

    def handle(self, *args, **options):
        database_path = Path(settings.DATABASES['default']['NAME']).resolve()
        if not database_path.exists():
            raise CommandError(f'База данных не найдена: {database_path}')

        output_dir = Path(options['output_dir']).resolve()
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        destination = output_dir / f'db-{timestamp}.sqlite3'

        source_connection = sqlite3.connect(
            f'file:{database_path}?mode=ro',
            uri=True,
            timeout=30,
        )
        backup_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(backup_connection)
            # Backup должен быть одним переносимым файлом. Иначе сохранённый
            # WAL-режим создаст рядом служебные -wal/-shm с иными правами.
            backup_connection.execute('PRAGMA journal_mode=DELETE')
        finally:
            backup_connection.close()
            source_connection.close()

        os.chmod(destination, 0o600)
        verification = sqlite3.connect(
            f'file:{destination}?mode=ro&immutable=1',
            uri=True,
        )
        try:
            result = verification.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            verification.close()
        if result != 'ok':
            destination.unlink(missing_ok=True)
            raise CommandError(f'Проверка backup завершилась ошибкой: {result}')

        cutoff = timezone.now() - timedelta(days=options['retain_days'])
        removed = 0
        for backup in output_dir.glob('db-*.sqlite3'):
            modified = datetime.fromtimestamp(
                backup.stat().st_mtime,
                tz=timezone.get_current_timezone(),
            )
            if modified < cutoff and backup != destination:
                backup.unlink()
                removed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Backup создан и проверен: {destination}; удалено старых: {removed}.'
            )
        )
