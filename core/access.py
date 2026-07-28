"""Единая проверка доступа к приложению после Telegram-аутентификации."""

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import InviteCode, ProjectMembership, TelegramUser


def is_telegram_id_allowed(telegram_id: int | None) -> bool:
    """Возвращает True, когда личный Telegram ID указан в .env."""
    return telegram_id is not None and telegram_id in settings.TELEGRAM_ALLOWED_IDS


def is_app_admin(user) -> bool:
    """Проверяет глобальное прикладное право администратора КружокAPP."""
    return bool(
        user.is_authenticated
        and (
            user.is_superuser
            or (
                user.telegram_id is not None
                and user.telegram_id in settings.TELEGRAM_ADMIN_IDS
            )
        )
    )


def user_has_access(user) -> bool:
    """Доступ дают либо allow-list, либо ранее активированное приглашение."""
    return bool(
        user.is_authenticated
        and (
            is_telegram_id_allowed(user.telegram_id)
            or is_app_admin(user)
            or user.is_verified
        )
    )


def redeem_invite_code(user, code) -> bool:
    """Атомарно погашает одноразовое приглашение для пользователя."""
    now = timezone.now()
    with transaction.atomic():
        invite = (
            InviteCode.objects.select_for_update()
            .filter(code=code, is_active=True, used_by__isnull=True)
            .filter(
                models.Q(expires_at__isnull=True)
                | models.Q(expires_at__gt=now)
            )
            .first()
        )
        if invite is None:
            return False

        TelegramUser.objects.filter(pk=user.pk).update(is_verified=True)
        InviteCode.objects.filter(pk=invite.pk).update(
            used_by=user,
            used_at=now,
            is_active=False,
        )
        if invite.project_id:
            ProjectMembership.objects.update_or_create(
                project_id=invite.project_id,
                user=user,
                defaults={'role': invite.project_role},
            )
        user.is_verified = True
        return True
