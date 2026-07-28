#!/bin/sh
set -eu
umask 077

interval="${BACKUP_INTERVAL_SECONDS:-86400}"
retain_days="${BACKUP_RETENTION_DAYS:-14}"

while true; do
    python manage.py backup_database \
        --output-dir /backups \
        --retain-days "$retain_days"
    date +%s > /tmp/backup-worker-heartbeat
    sleep "$interval"
done
