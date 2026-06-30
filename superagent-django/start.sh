#!/bin/bash
# Start Celery worker in the background
celery -A superagent worker --loglevel=info --concurrency 1 &

# Start gunicorn in the foreground (keeps the service alive)
gunicorn superagent.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
