#!/bin/sh
set -eu
umask 077

if [ -f "${DATABASE_PATH:-/app/data/db.sqlite3}" ]; then
    python manage.py backup_database \
        --output-dir /backups \
        --retain-days "${BACKUP_RETENTION_DAYS:-14}"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
