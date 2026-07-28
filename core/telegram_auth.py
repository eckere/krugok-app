"""
core/telegram_auth.py

Валидация initData, которую Telegram передаёт фронтенду при открытии
Mini App (window.Telegram.WebApp.initData). Алгоритм — официальный:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

Коротко, что происходит:
  1. Из initData убираем поле hash, остальное сортируем по ключу и
     склеиваем в "data-check-string".
  2. secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token) — это
     фиксированная соль, которую требует протокол Telegram.
  3. Заново считаем HMAC_SHA256(key=secret_key, msg=data_check_string)
     и сравниваем с присланным hash. Совпало — данные точно от Telegram
     и не подделаны, т.к. bot_token знают только наш сервер и Telegram.
"""
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl

from django.conf import settings

# Старше этого — считаем initData протухшим и отклоняем (защита от replay).
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60
INVITE_START_PARAM_RE = re.compile(r'^invite_([A-Za-z0-9_-]{1,64})$')


class InitDataValidationError(Exception):
    """Подпись initData неверна, данные повреждены или устарели."""


class LoginWidgetValidationError(Exception):
    """Данные обычного Telegram Login Widget нельзя считать подлинными."""


def validate_init_data(init_data: str, bot_token: str | None = None) -> dict:
    """
    Проверяет подпись initData и возвращает распарсенные данные пользователя.

    :param init_data: сырая строка window.Telegram.WebApp.initData
    :param bot_token: токен бота; по умолчанию settings.TELEGRAM_BOT_TOKEN
    :raises InitDataValidationError: подпись не совпала / данные устарели /
                                      не хватает обязательных полей
    :return: dict с полями user из initData (id, first_name, username, ...)
    """
    bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN

    if not init_data:
        raise InitDataValidationError('initData пустой')

    # parse_qsl уже делает url-decode значений — руками decode вызывать не нужно
    data = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = data.pop('hash', None)
    if not received_hash:
        raise InitDataValidationError('В initData отсутствует поле hash')

    data_check_string = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))

    secret_key = hmac.new(
        key=b'WebAppData',
        msg=bot_token.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()

    computed_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Сравнение строго constant-time — hmac.compare_digest, а не "=="
    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataValidationError('Неверная подпись initData')

    try:
        auth_date = int(data.get('auth_date', 0))
    except ValueError:
        raise InitDataValidationError('Некорректное поле auth_date')

    now = time.time()
    if auth_date > now + 60 or now - auth_date > MAX_INIT_DATA_AGE_SECONDS:
        raise InitDataValidationError('initData устарел')

    user_raw = data.get('user')
    if not user_raw:
        raise InitDataValidationError('В initData отсутствуют данные пользователя')

    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InitDataValidationError('Не удалось распарсить поле user') from exc

    if not isinstance(user_data, dict):
        raise InitDataValidationError('Данные пользователя имеют неверный формат')
    try:
        telegram_id = int(user_data['id'])
    except (KeyError, TypeError, ValueError) as exc:
        raise InitDataValidationError(
            'В данных пользователя отсутствует корректный id'
        ) from exc
    if telegram_id <= 0 or telegram_id > 2**63 - 1:
        raise InitDataValidationError('Некорректный Telegram ID')
    user_data['id'] = telegram_id

    return user_data


def extract_invite_code_from_start_param(start_param: str) -> str | None:
    """Проверяет формат Telegram startapp-параметра приглашения."""
    match = INVITE_START_PARAM_RE.fullmatch(start_param)
    return match.group(1) if match else None


def extract_invite_code(init_data: str) -> str | None:
    """Извлекает код приглашения из уже проверенного Telegram initData."""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    return extract_invite_code_from_start_param(data.get('start_param', ''))


def validate_login_widget_data(
    auth_data: Mapping[str, object], bot_token: str | None = None
) -> dict:
    """Проверяет HMAC-подпись данных из Telegram Login Widget.

    Для виджета Telegram использует другой ключ, чем Mini App: SHA-256 от
    токена бота. Нельзя переиспользовать ``validate_init_data`` — иначе
    правильный ответ виджета будет отклонён, а неверная схема проверки может
    допустить поддельный вход.
    """
    bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN

    if not isinstance(auth_data, Mapping):
        raise LoginWidgetValidationError('Данные виджета отсутствуют')

    received_hash = auth_data.get('hash')
    if not isinstance(received_hash, str) or not received_hash:
        raise LoginWidgetValidationError('В данных виджета отсутствует hash')

    try:
        telegram_id = int(auth_data['id'])
        auth_date = int(auth_data['auth_date'])
    except (KeyError, TypeError, ValueError) as exc:
        raise LoginWidgetValidationError('В данных виджета нет корректных id или auth_date') from exc
    if telegram_id <= 0 or telegram_id > 2**63 - 1:
        raise LoginWidgetValidationError('Некорректный Telegram ID')

    signed_data = {
        str(key): str(value)
        for key, value in auth_data.items()
        if key != 'hash'
    }
    data_check_string = '\n'.join(
        f'{key}={value}' for key, value in sorted(signed_data.items())
    )
    secret_key = hashlib.sha256(bot_token.encode('utf-8')).digest()
    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise LoginWidgetValidationError('Неверная подпись данных виджета')

    now = time.time()
    if auth_date > now + 60 or now - auth_date > MAX_INIT_DATA_AGE_SECONDS:
        raise LoginWidgetValidationError('Данные виджета устарели')

    return {
        'id': telegram_id,
        'username': str(auth_data.get('username') or ''),
        'first_name': str(auth_data.get('first_name') or ''),
        'last_name': str(auth_data.get('last_name') or ''),
        'photo_url': str(auth_data.get('photo_url') or '') or None,
    }
