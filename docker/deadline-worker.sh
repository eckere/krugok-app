#!/bin/sh
set -eu
umask 077

deadline_interval="${DEADLINE_CHECK_INTERVAL_SECONDS:-900}"
outbound_interval="${OUTBOUND_POLL_INTERVAL_SECONDS:-5}"
next_deadline=0

while true; do
    now="$(date +%s)"
    if [ "$now" -ge "$next_deadline" ]; then
        python manage.py check_deadlines
        next_deadline=$((now + deadline_interval))
    else
        python manage.py process_notifications --limit 20
    fi
    date +%s > /tmp/deadline-worker-heartbeat
    sleep "$outbound_interval"
done
