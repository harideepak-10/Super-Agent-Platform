#!/bin/bash
# Start Celery worker in background
celery -A superagent worker --loglevel=info --concurrency 1 &

# Start Celery beat (scheduled tasks) in background
celery -A superagent beat --loglevel=info &

# Start gunicorn in foreground (keeps the service alive)
gunicorn superagent.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
