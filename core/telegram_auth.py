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
import time
from urllib.parse import parse_qsl

from django.conf import settings

# Старше этого — считаем initData протухшим и отклоняем (защита от replay).
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60


class InitDataValidationError(Exception):
    """Подпись initData неверна, данные повреждены или устарели."""


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

    if time.time() - auth_date > MAX_INIT_DATA_AGE_SECONDS:
        raise InitDataValidationError('initData устарел')

    user_raw = data.get('user')
    if not user_raw:
        raise InitDataValidationError('В initData отсутствуют данные пользователя')

    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InitDataValidationError('Не удалось распарсить поле user') from exc

    if 'id' not in user_data:
        raise InitDataValidationError('В данных пользователя отсутствует id')

    return user_data
