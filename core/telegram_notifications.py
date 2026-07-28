import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Notification, OutboundMessage, Task, TelegramUser

BOT_API_TIMEOUT_SECONDS = 10


def _bot_api_error(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        description = payload.get('description')
        if description:
            return str(description)
    return fallback


def send_telegram_message(chat_id: int | None, text: str) -> tuple[bool, str]:
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise ValueError('TELEGRAM_BOT_TOKEN не задан.')
    if chat_id is None:
        return False, 'У получателя отсутствует telegram_id.'

    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(
            {
                'chat_id': str(chat_id),
                'text': text,
            }
        ).encode('utf-8'),
        method='POST',
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=BOT_API_TIMEOUT_SECONDS,
        ) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        return False, _bot_api_error(
            payload,
            f'Bot API вернул HTTP {exc.code}: {exc.reason}',
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f'Ошибка соединения с Bot API: {exc}'

    try:
        payload = json.loads(response_body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f'Некорректный ответ Bot API: {exc}'

    if payload.get('ok') is True:
        return True, ''
    return False, _bot_api_error(payload, 'Bot API вернул ok=false.')


def _format_deadline(task: Task, recipient: TelegramUser | None = None) -> str:
    if not task.deadline:
        return ''
    try:
        tz = ZoneInfo(recipient.timezone) if recipient else timezone.get_current_timezone()
    except ZoneInfoNotFoundError:
        tz = timezone.get_current_timezone()
    return timezone.localtime(task.deadline, tz).strftime('%d.%m.%Y %H:%M')


def _notification_text(
    task: Task,
    kind: str,
    recipient: TelegramUser | None = None,
) -> str:
    messages = {
        Notification.Kind.DEADLINE_SET: (
            f'Задаче «{task.title}» назначен дедлайн '
            f'{_format_deadline(task, recipient)}.'
        ),
        Notification.Kind.DEADLINE_APPROACHING: (
            f'Дедлайн задачи «{task.title}» — менее чем через 24 часа.'
        ),
        Notification.Kind.DEADLINE_OVERDUE: (
            f'Задача «{task.title}» просрочена.'
        ),
        Notification.Kind.TASK_ASSIGNED: (
            f'Вам назначена задача «{task.title}».'
        ),
    }
    try:
        return messages[kind]
    except KeyError as exc:
        raise ValueError(f'Неизвестный вид уведомления: {kind}') from exc


def _recipient_allows(recipient: TelegramUser, kind: str) -> bool:
    if kind in {
        Notification.Kind.DEADLINE_SET,
        Notification.Kind.DEADLINE_APPROACHING,
        Notification.Kind.DEADLINE_OVERDUE,
    }:
        return recipient.notify_deadlines
    if kind == Notification.Kind.TASK_ASSIGNED:
        return recipient.notify_assignments
    if kind == Notification.Kind.COMMENT_ADDED:
        return recipient.notify_comments
    if kind == Notification.Kind.MESSAGE_ADDED:
        return recipient.notify_messages
    return True


def enqueue_outbound(
    *,
    recipient: TelegramUser,
    kind: str,
    text: str,
    dedupe_key: str,
    notification: Notification | None = None,
) -> OutboundMessage | None:
    if recipient.telegram_id is None or not _recipient_allows(recipient, kind):
        return None
    outbound, _created = OutboundMessage.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            'recipient': recipient,
            'notification': notification,
            'kind': kind,
            'text': text,
            'next_retry_at': timezone.now(),
        },
    )
    return outbound


@transaction.atomic
def notify(task: Task, kind: str) -> Notification | None:
    """Только фиксирует доставку; сетевой вызов выполняет worker."""
    recipient = task.assignee or task.creator
    if recipient.telegram_id is None or not _recipient_allows(recipient, kind):
        return None
    notification, created = Notification.objects.get_or_create(
        task=task,
        kind=kind,
        defaults={'recipient': recipient},
    )

    if not created and notification.recipient_id != recipient.id:
        notification.recipient = recipient
        notification.status = Notification.Status.PENDING
        notification.error_message = ''
        notification.attempt_count = 0
        notification.last_attempt_at = None
        notification.next_retry_at = timezone.now()
        notification.sent_at = None
        notification.save(
            update_fields=[
                'recipient',
                'status',
                'error_message',
                'attempt_count',
                'last_attempt_at',
                'next_retry_at',
                'sent_at',
            ]
        )
    elif not created:
        if notification.status == Notification.Status.SENT:
            return notification
        if notification.attempt_count >= settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS:
            return notification
    notification.status = Notification.Status.PENDING
    notification.next_retry_at = notification.next_retry_at or timezone.now()
    notification.save(update_fields=['status', 'next_retry_at'])
    enqueue_outbound(
        recipient=recipient,
        kind=kind,
        text=_notification_text(task, kind, recipient),
        dedupe_key=f'notification:{notification.pk}:{recipient.pk}',
        notification=notification,
    )
    return notification


def enqueue_task_event(
    task: Task,
    *,
    recipient: TelegramUser,
    kind: str,
    event_id: str,
    text: str,
) -> OutboundMessage | None:
    return enqueue_outbound(
        recipient=recipient,
        kind=kind,
        text=text,
        dedupe_key=f'{kind}:{event_id}:{recipient.pk}',
    )


def deliver_outbound(outbound_id: int) -> OutboundMessage:
    now = timezone.now()
    with transaction.atomic():
        outbound = OutboundMessage.objects.select_for_update().select_related(
            'recipient', 'notification'
        ).get(pk=outbound_id)
        if outbound.status == OutboundMessage.Status.SENT:
            return outbound
        if outbound.attempt_count >= settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS:
            return outbound
        if outbound.next_retry_at and outbound.next_retry_at > now:
            return outbound
        outbound.status = OutboundMessage.Status.SENDING
        outbound.attempt_count += 1
        outbound.last_attempt_at = now
        outbound.save(
            update_fields=['status', 'attempt_count', 'last_attempt_at']
        )

    success, error_message = send_telegram_message(
        outbound.recipient.telegram_id,
        outbound.text,
    )
    finished_at = timezone.now()
    with transaction.atomic():
        outbound = OutboundMessage.objects.select_for_update().select_related(
            'notification'
        ).get(pk=outbound_id)
        outbound.status = (
            OutboundMessage.Status.SENT
            if success
            else OutboundMessage.Status.FAILED
        )
        outbound.error_message = error_message
        outbound.sent_at = finished_at if success else None
        outbound.next_retry_at = None
        if (
            not success
            and outbound.attempt_count
            < settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS
        ):
            outbound.next_retry_at = finished_at + timedelta(
                seconds=settings.TELEGRAM_NOTIFICATION_RETRY_SECONDS
            )
        outbound.save(
            update_fields=[
                'status',
                'error_message',
                'sent_at',
                'next_retry_at',
            ]
        )
        if outbound.notification_id:
            notification = outbound.notification
            notification.status = (
                Notification.Status.SENT
                if success
                else Notification.Status.FAILED
            )
            notification.error_message = error_message
            notification.attempt_count = outbound.attempt_count
            notification.last_attempt_at = outbound.last_attempt_at
            notification.next_retry_at = outbound.next_retry_at
            notification.sent_at = outbound.sent_at
            notification.save(
                update_fields=[
                    'status',
                    'error_message',
                    'attempt_count',
                    'last_attempt_at',
                    'next_retry_at',
                    'sent_at',
                ]
            )
    return outbound


def process_outbound(*, limit: int = 100) -> tuple[int, int]:
    now = timezone.now()
    OutboundMessage.objects.filter(
        status=OutboundMessage.Status.SENDING,
        last_attempt_at__lt=now - timedelta(minutes=10),
    ).update(
        status=OutboundMessage.Status.FAILED,
        error_message='Предыдущая попытка прервана; доставка возвращена в очередь.',
        next_retry_at=now,
    )
    ids = list(
        OutboundMessage.objects.filter(
            Q(status=OutboundMessage.Status.PENDING)
            | Q(status=OutboundMessage.Status.FAILED),
            attempt_count__lt=settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS,
        )
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .order_by('created_at')
        .values_list('pk', flat=True)[:limit]
    )
    sent = failed = 0
    for outbound_id in ids:
        result = deliver_outbound(outbound_id)
        if result.status == OutboundMessage.Status.SENT:
            sent += 1
        elif result.status == OutboundMessage.Status.FAILED:
            failed += 1
    return sent, failed
