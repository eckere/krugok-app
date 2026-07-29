# Production deployment

## Требования

- Ubuntu 24.04 или актуальная Debian/Ubuntu;
- Docker Engine и Docker Compose plugin;
- постоянный публичный IPv4 и DNS A-запись;
- открытые TCP 22, 80 и 443; UDP 443 используется HTTP/3 и необязателен;
- отдельный Telegram bot token, не публиковавшийся в Git или переписках.

## Подготовка

Клонируйте приложение в `/opt/krugok-app`, создайте каталог резервных копий с
фиксированным UID контейнера и закройте production-конфигурацию от чтения:

```sh
sudo install -d -o 10001 -g 10001 -m 700 /opt/krugok-backups
cp .env.production.example .env.production
chmod 600 .env.production
```

Заполните все значения в `.env.production`. `APP_DOMAIN`, `ALLOWED_HOSTS` и
`CSRF_TRUSTED_ORIGINS` должны описывать один реальный HTTPS-домен.
`BACKUP_HOST_DIR` должен указывать на подготовленный каталог. Секрет можно
создать так:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Укажите реального оператора и рабочие контакты в `PRIVACY_OPERATOR_NAME`,
`PRIVACY_CONTACT` и `SUPPORT_CONTACT`. Для Telegram задайте username без `@`,
зарегистрируйте домен через BotFather `/setdomain` и включите Main Mini App на
`https://<APP_DOMAIN>/`. `TELEGRAM_API_IPV4` фиксирует официальный IPv4 Bot
API для провайдеров с нерабочим маршрутом к DNS-ответу Telegram; перед
развёртыванием проверьте адрес командой из `OPERATIONS.md`.

Если UFW настроен с `deny routed`, разрешите только исходящий трафик Docker
через внешний интерфейс, иначе уведомления не достигнут Telegram:

```sh
external_iface="$(ip route show default | awk '{print $5; exit}')"
sudo ufw route allow out on "$external_iface" from 172.16.0.0/12
```

## Первый запуск

```sh
APP_RELEASE_SHA="$(git rev-parse --short=12 HEAD)" \
  docker compose --project-name krugok-app --env-file .env.production config --quiet
APP_RELEASE_SHA="$(git rev-parse --short=12 HEAD)" \
  docker compose --project-name krugok-app --env-file .env.production up -d --build --wait
docker compose --project-name krugok-app ps
docker compose --project-name krugok-app logs --tail=150 web deadline-worker backup-worker caddy
curl -fsS "https://${APP_DOMAIN}/healthz/"
curl -fsS "https://${APP_DOMAIN}/readyz/"
```

Caddy получает сертификат автоматически. `web` перед стартом создаёт
консистентную резервную копию существующей БД, применяет миграции, собирает
статику и запускает `check --deploy`. Worker уведомлений работает с интервалом
`DEADLINE_CHECK_INTERVAL_SECONDS`, backup worker — с интервалом
`BACKUP_INTERVAL_SECONDS`.

## Безопасное обновление

Сначала обновите рабочую копию до нужного commit, затем запустите штатный
сценарий релиза:

```sh
git pull --ff-only
sh docker/deploy.sh
curl -fsS "https://${APP_DOMAIN}/readyz/"
```

Скрипт всегда использует Compose project `krugok-app`, поэтому запуск из другого
каталога не создаст новые пустые тома. Перед обновлением он делает резервную
копию SQLite и сохраняет текущий образ под меткой `rollback-*`. Затем ожидает
healthcheck всех сервисов, запускает эксплуатационную проверку и внутренний
smoke-test `/readyz/`. Если проверка падает, предыдущий образ возвращается
автоматически; при несовместимой миграции БД восстановите созданную копию по
инструкции из `OPERATIONS.md`. Известные ошибки очереди выводятся предупреждением
и не блокируют установку версии с экраном для их разбора; повреждение БД всё
равно прерывает релиз.

Не удаляйте rollback-образ до отдельной проверки интерфейса. После успешной
проверки:

```sh
docker image prune -f
```

Откат кода выполняется возвратом на предыдущий Git commit и повторным
`docker compose up -d --build`. Если миграция необратимо изменила данные,
восстанавливайте проверенную копию по инструкции из `OPERATIONS.md`.
