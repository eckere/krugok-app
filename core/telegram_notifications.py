import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Notification, Task


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
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        return False, f'Ошибка соединения с Bot API: {exc}'

    try:
        payload = json.loads(response_body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f'Некорректный ответ Bot API: {exc}'

    if payload.get('ok') is True:
        return True, ''
    return False, _bot_api_error(payload, 'Bot API вернул ok=false.')


def _format_deadline(task: Task) -> str:
    if not task.deadline:
        return ''
    return timezone.localtime(task.deadline).strftime('%d.%m.%Y %H:%M')


def _notification_text(task: Task, kind: str) -> str:
    messages = {
        Notification.Kind.DEADLINE_SET: (
            f'Задаче «{task.title}» назначен дедлайн '
            f'{_format_deadline(task)}.'
        ),
        Notification.Kind.DEADLINE_APPROACHING: (
            f'Дедлайн задачи «{task.title}» — менее чем через 24 часа.'
        ),
        Notification.Kind.DEADLINE_OVERDUE: (
            f'Задача «{task.title}» просрочена.'
        ),
    }
    try:
        return messages[kind]
    except KeyError as exc:
        raise ValueError(f'Неизвестный вид уведомления: {kind}') from exc


def notify(task: Task, kind: str) -> Notification | None:
    recipient = task.assignee or task.creator
    now = timezone.now()
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
        notification.next_retry_at = None
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
        if (
            notification.status == Notification.Status.PENDING
            and notification.attempt_count > 0
        ):
            return notification
        if notification.attempt_count >= settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS:
            return notification
        if notification.next_retry_at and notification.next_retry_at > now:
            return notification

    success, error_message = send_telegram_message(
        recipient.telegram_id,
        _notification_text(task, kind),
    )
    notification.attempt_count += 1
    notification.last_attempt_at = now
    notification.status = (
        Notification.Status.SENT if success else Notification.Status.FAILED
    )
    notification.error_message = error_message
    notification.sent_at = now if success else None
    notification.next_retry_at = None
    if (
        not success
        and notification.attempt_count
        < settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS
    ):
        notification.next_retry_at = now + timedelta(
            seconds=settings.TELEGRAM_NOTIFICATION_RETRY_SECONDS
        )
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
    return notification
