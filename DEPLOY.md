# КружокAPP: VPS deployment

## Requirements

- Ubuntu/Debian VPS with Docker Engine and Docker Compose plugin.
- A DNS A record pointing `APP_DOMAIN` to the VPS public IPv4 address.
- Open inbound TCP ports 22, 80 and 443; UDP 443 is optional.
- A fresh Telegram bot token. Never deploy a token that has been published.

## First deployment

1. Copy or clone the repository to `/opt/krugok-app`.
2. Copy `.env.production.example` to `.env.production`.
3. Replace every placeholder and set the real domain in `APP_DOMAIN`,
   `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. Add the personal Telegram IDs
   that may enter without an invitation to `TELEGRAM_ALLOWED_IDS` (comma-separated).
   To enable Telegram login in a normal browser, set `TELEGRAM_BOT_USERNAME`
   and register `https://<APP_DOMAIN>` for that bot with @BotFather using
   `/setdomain`. In @BotFather, also enable the bot's Main Mini App with
   `https://<APP_DOMAIN>/` as its URL; invitation links use its signed
   `startapp` parameter.
4. Start the stack:

   ```sh
   docker compose --env-file .env.production up -d --build
   ```

5. Check the containers and application:

   ```sh
   docker compose ps
   docker compose logs --tail=100 web caddy deadline-worker
   curl -fsS "https://${APP_DOMAIN}/healthz/"
   ```

Caddy obtains and renews the HTTPS certificate automatically. The web
container runs migrations and `collectstatic` before starting Gunicorn.
`deadline-worker` checks deadlines every 15 minutes by default.

## Updating

```sh
git pull --ff-only
docker compose --env-file .env.production up -d --build
docker image prune -f
```

## SQLite backup

Before an update, stop application writers and copy the named volume:

```sh
docker compose stop web deadline-worker
docker run --rm \
  -v krugok-app_sqlite_data:/data:ro \
  -v /opt/krugok-backups:/backup \
  alpine \
  cp /data/db.sqlite3 "/backup/db-$(date +%Y%m%d-%H%M%S).sqlite3"
docker compose start web deadline-worker
```

The exact Docker volume prefix can differ. Verify it first with
`docker volume ls`.
