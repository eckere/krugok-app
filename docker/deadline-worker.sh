#!/bin/sh
set -eu

interval="${DEADLINE_CHECK_INTERVAL_SECONDS:-900}"

while true; do
    python manage.py check_deadlines
    sleep "$interval"
done
