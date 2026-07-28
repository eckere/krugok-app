# КружокAPP

Небольшое командное приложение для Telegram Mini App: проекты, этапы, задачи,
комментарии и обсуждения. Авторизация выполняется по подписанным данным
Telegram, доступ выдаётся по списку разрешённых ID или одноразовому приглашению.

## Стек

- Python 3.12, Django 6;
- SQLite в WAL-режиме;
- HTMX и обычный серверный HTML;
- Gunicorn, Caddy, Docker Compose;
- отдельные worker-процессы для уведомлений и резервных копий.

## Локальный запуск

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.production.example .env
```

Для локального файла `.env` установите `DEBUG=True`, локальный `SECRET_KEY`,
тестовый `TELEGRAM_BOT_TOKEN` и `ALLOWED_HOSTS=localhost,127.0.0.1,testserver`.
Затем:

```powershell
python manage.py migrate
python manage.py runserver
```

При `DEBUG=True` доступен локальный тестовый вход `/dev-login/`. В production
этот маршрут возвращает 404.

## Проверки

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Production-развёртывание описано в [DEPLOY.md](DEPLOY.md), эксплуатация и
восстановление — в [OPERATIONS.md](OPERATIONS.md), правила безопасности — в
[SECURITY.md](SECURITY.md).

## Важные свойства

- SQLite остаётся единственным хранилищем; короткие записи сериализуются через
  `BEGIN IMMEDIATE`, порядок этапов меняется транзакционно.
- Опасные сущности сначала архивируются. Окончательное удаление проекта или
  этапа доступно владельцу только из архива.
- Telegram-уведомления записываются в durable outbox и отправляются worker-ом,
  поэтому сбой Bot API не откатывает пользовательскую операцию.
- Профили, исполнители и участники обсуждений ограничены существующим рабочим
  контекстом; глобальный администратор видит всё.
- Журнал аудита хранит значимые изменения и хешированный IP, но не исходный IP.
