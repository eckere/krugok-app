from django.core.management.base import BaseCommand

from core.telegram_notifications import process_outbound


class Command(BaseCommand):
    help = 'Отправляет накопившиеся сообщения из durable outbox.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)

    def handle(self, *args, **options):
        limit = max(1, min(options['limit'], 200))
        sent, failed = process_outbound(limit=limit)
        if sent or failed:
            self.stdout.write(f'Отправлено: {sent}; ошибок: {failed}.')
