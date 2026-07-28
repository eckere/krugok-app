# Эксплуатация

## Ежедневная проверка

```sh
docker compose ps
docker compose logs --since=24h web deadline-worker backup-worker caddy
docker compose exec -T web python manage.py check_operational_health
curl -fsS "https://${APP_DOMAIN}/readyz/"
find "${BACKUP_HOST_DIR}" -maxdepth 1 -name 'db-*.sqlite3' -mtime -2 -print
df -h
```

`healthz` проверяет доступность Django и БД. `readyz` дополнительно запускает
SQLite `integrity_check`. `/ops/status/` доступен только глобальному
администратору приложения и показывает состояние очереди.

## Резервные копии

Backup worker использует SQLite backup API и проверяет каждую копию через
`PRAGMA integrity_check`. По умолчанию создаётся одна копия в сутки, срок
хранения — 14 дней. Каталог находится вне Docker volume.

Не реже раза в месяц копируйте свежую резервную копию в отдельное хранилище или
Yandex Object Storage. Копия на том же диске не защищает от потери ВМ.

Рекомендуемый внешний контроль: оповещать, если `backup-worker` unhealthy,
последняя копия старше 26 часов, `/readyz/` не отвечает или свободно меньше 20%
диска.

## Проверка восстановления

Проводите её после изменения миграций и не реже раза в месяц:

```sh
latest="$(find "${BACKUP_HOST_DIR}" -maxdepth 1 -name 'db-*.sqlite3' -printf '%T@ %p\n' \
  | sort -nr | head -1 | cut -d' ' -f2-)"
test -n "$latest"
docker run --rm -v "${latest}:/restore/db.sqlite3:ro" python:3.12-slim \
  python -c "import sqlite3; c=sqlite3.connect('file:/restore/db.sqlite3?mode=ro', uri=True); print(c.execute('pragma integrity_check').fetchone()[0])"
```

Для полного учебного восстановления используйте отдельный временный Docker
volume и отдельный Compose project. Не проверяйте restore поверх production.

## Аварийное восстановление

1. Остановите writers: `docker compose stop web deadline-worker backup-worker`.
2. Скопируйте повреждённый файл БД отдельно для анализа.
3. Выберите последнюю проверенную копию.
4. Узнайте volume: `docker volume ls | grep sqlite_data`.
5. Очистите только файл `db.sqlite3`, не весь volume, и скопируйте backup.
6. Запустите `web`, проверьте миграции и `/readyz/`, затем workers и Caddy.

Пример копирования в volume, где `VOLUME_NAME` предварительно проверен вручную:

```sh
docker run --rm \
  -v VOLUME_NAME:/data \
  -v "${BACKUP_HOST_DIR}:/backup:ro" \
  alpine sh -c 'cp /backup/db-YYYYMMDD-HHMMSS.sqlite3 /data/db.sqlite3 && chmod 600 /data/db.sqlite3'
```

## Инциденты уведомлений

Проверьте `docker compose logs deadline-worker` и `/ops/status/`. После
устранения сетевой ошибки записи со статусом `failed`, не исчерпавшие число
попыток, будут отправлены повторно. Исчерпанные записи сохраняются для разбора;
не меняйте их напрямую без резервной копии.

Проверьте доступ к Bot API из контейнера, не выводя токен:

```sh
docker compose exec -T web python -c \
  "import socket; print(socket.create_connection(('api.telegram.org', 443), 10).getpeername())"
```

Если маршрут до закреплённого адреса перестал работать, найдите доступный
официальный адрес `api.telegram.org`, проверьте TLS через
`curl --resolve api.telegram.org:443:<IP> https://api.telegram.org/` и
обновите `TELEGRAM_API_IPV4`. Не используйте сторонний HTTP-прокси: он получит
доступ к bot token.

## Ресурсы SQLite

При небольшом количестве пользователей 2 vCPU и 2 ГБ RAM достаточны. Признаки,
что пора пересмотреть архитектуру: устойчивые блокировки БД, несколько
экземпляров web на разных машинах, сотни одновременных активных пользователей
или необходимость аналитических запросов по большой истории.
