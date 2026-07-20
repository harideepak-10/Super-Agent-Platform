#!/bin/bash
# Celery worker — auto-restarts if it crashes
(while true; do
    celery -A superagent worker --loglevel=info --concurrency 1
    echo "worker died, restarting in 5s"
    sleep 5
done) &

# Celery beat — auto-restarts if it crashes
(while true; do
    celery -A superagent beat --loglevel=info
    echo "beat died, restarting in 5s"
    sleep 5
done) &

# Gunicorn in foreground (keeps the service alive)
gunicorn superagent.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --timeout 120
