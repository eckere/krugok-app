from django.conf import settings

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
