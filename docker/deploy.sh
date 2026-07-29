#!/bin/sh
set -eu

project_name='krugok-app'
env_file='.env.production'
rollback_tag=''
previous_release='unknown'
deployment_complete='false'

compose() {
    docker compose --project-name "$project_name" --env-file "$env_file" "$@"
}

rollback() {
    exit_code=$?
    if [ "$deployment_complete" = 'true' ] || [ -z "$rollback_tag" ]; then
        exit "$exit_code"
    fi

    trap - EXIT INT TERM
    set +e
    echo "Проверка релиза не прошла. Возвращаю предыдущий образ $rollback_tag."
    docker tag "$rollback_tag" krugok-app:latest
    APP_RELEASE_SHA="$previous_release" compose up -d --no-build --force-recreate \
        web deadline-worker backup-worker
    echo "Код возвращён на предыдущий образ. Если миграция была несовместимой,"
    echo "восстановите созданную перед релизом копию БД по OPERATIONS.md."
    exit "$exit_code"
}

trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ ! -f "$env_file" ]; then
    echo "Не найден $env_file." >&2
    exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Скрипт нужно запускать из корня Git-репозитория." >&2
    exit 2
fi

release_sha="$(git rev-parse --short=12 HEAD)"
running_web="$(compose ps -q web 2>/dev/null || true)"

if [ -n "$running_web" ]; then
    previous_image="$(docker inspect --format '{{.Image}}' "$running_web")"
    previous_release="$(
        compose exec -T web printenv APP_RELEASE_SHA 2>/dev/null || printf 'unknown'
    )"
    rollback_tag="krugok-app:rollback-$(date -u +%Y%m%d-%H%M%S)"
    docker tag "$previous_image" "$rollback_tag"
    compose exec -T web \
        python manage.py backup_database --output-dir /backups --retain-days 14
fi

APP_RELEASE_SHA="$release_sha" compose config --quiet
APP_RELEASE_SHA="$release_sha" compose build
APP_RELEASE_SHA="$release_sha" compose up -d --wait --remove-orphans
APP_RELEASE_SHA="$release_sha" compose exec -T web \
    python manage.py check_operational_health --allow-queue-degraded
APP_RELEASE_SHA="$release_sha" compose exec -T web python -c \
    "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/readyz/',headers={'Host':os.environ['APP_DOMAIN'],'X-Forwarded-Proto':'https'}); urllib.request.urlopen(r,timeout=5)"

deployment_complete='true'
trap - EXIT INT TERM
echo "Релиз $release_sha запущен. Образ для ручного отката: ${rollback_tag:-не создавался}."
