import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "superagent.settings.development")

app = Celery("superagent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
