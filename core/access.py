"""Единая проверка доступа к приложению после Telegram-аутентификации."""

from django.conf import settings


def is_telegram_id_allowed(telegram_id: int | None) -> bool:
    """Возвращает True, когда личный Telegram ID указан в .env."""
    return telegram_id is not None and telegram_id in settings.TELEGRAM_ALLOWED_IDS


def user_has_access(user) -> bool:
    """Доступ дают либо allow-list, либо ранее активированное приглашение."""
    return bool(
        user.is_authenticated
        and (is_telegram_id_allowed(user.telegram_id) or user.is_verified)
    )
