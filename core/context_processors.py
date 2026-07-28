from django.conf import settings

from .access import is_app_admin, user_has_access
from .models import TelegramUser


def dev_accounts(request):
    """Добавляет тестовые аккаунты в интерфейс только при локальной разработке."""
    if not settings.DEBUG or not request.user.is_authenticated:
        return {
            'dev_account_switcher_enabled': False,
            'dev_accounts': (),
        }

    return {
        'dev_account_switcher_enabled': True,
        'dev_accounts': TelegramUser.objects.filter(is_active=True).order_by(
            'first_name',
            'last_name',
            'username',
        ),
    }


def telegram_login(request):
    """Настройки, безопасные для передачи в шаблоны авторизации."""
    return {
        'telegram_bot_username': settings.TELEGRAM_BOT_USERNAME,
        'telegram_login_enabled': bool(settings.TELEGRAM_BOT_USERNAME),
        'user_can_access': user_has_access(request.user),
        'is_app_admin': is_app_admin(request.user),
        'dev_login_enabled': settings.DEBUG,
    }
