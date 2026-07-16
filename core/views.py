"""
core/views.py
"""
import json
import logging

from django.contrib.auth import login
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .models import TelegramUser
from .telegram_auth import InitDataValidationError, validate_init_data

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
def index(request):
    """
    GET /
    Точка входа Mini App. Telegram открывает именно этот URL внутри своего
    WebView. Шаблон грузит telegram-web-app.js и на DOMContentLoaded сам
    шлёт initData на /auth/telegram/ (см. templates/base.html).

    @ensure_csrf_cookie — гарантирует, что кука csrftoken уйдёт клиенту
    даже до захода на страницы с формами; без неё POST на /auth/telegram/
    будет 403 (кука появляется лениво только при первом её использовании).
    """
    return render(request, 'core/index.html')


@require_POST
def auth_telegram(request):
    """
    POST /auth/telegram/
    Тело запроса (JSON): {"init_data": "<window.Telegram.WebApp.initData>"}

    Валидирует initData -> находит/создаёт TelegramUser -> логинит его
    через обычный django.contrib.auth сессионный механизм. Дальше все
    HTMX-запросы едут по стандартной cookie-сессии, отдельный токен не нужен.

    CSRF здесь НЕ отключаем: страница со скриптом Telegram WebApp SDK уже
    отдана нашим сервером (см. base.html), поэтому кука csrftoken к моменту
    этого fetch() уже есть у клиента.
    """
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Некорректное тело запроса'}, status=400)

    init_data = payload.get('init_data', '')

    try:
        tg_user = validate_init_data(init_data)
    except InitDataValidationError as exc:
        logger.warning('Отклонена попытка входа в Mini App: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=403)

    telegram_id = tg_user['id']
    username = tg_user.get('username') or f'tg_{telegram_id}'

    user, _created = TelegramUser.objects.update_or_create(
        telegram_id=telegram_id,
        defaults={
            'username': username,
            'first_name': tg_user.get('first_name', ''),
            'last_name': tg_user.get('last_name', ''),
            'photo_url': tg_user.get('photo_url'),
        },
    )

    login(request, user)

    return JsonResponse({
        'ok': True,
        'user': {'id': user.id, 'display_name': user.display_name},
    })
