"""
Production settings for Render deployment.

Render injects these automatically when you attach services:
  DATABASE_URL  — Postgres connection string
  PORT          — Port gunicorn binds to

Set these manually in Render → Environment:
  SECRET_KEY
  DJANGO_SETTINGS_MODULE = superagent.settings.production
  ALLOWED_HOSTS          e.g. your-app.onrender.com
  REDIS_URL              from upstash.com (free)
  GROQ_API_KEY
  CORS_ALLOWED_ORIGINS   once your mobile app is live
"""

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*.onrender.com"])

SECRET_KEY = env("SECRET_KEY")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
# Database — Render injects DATABASE_URL automatically
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL")
}

# ---------------------------------------------------------------------------
# Static files — WhiteNoise
# ---------------------------------------------------------------------------
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Redis — Celery broker + Django Channels (use Upstash free tier)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="memory://")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL if REDIS_URL != "memory://" else "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True   # silence deprecation warning

if REDIS_URL != "memory://":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }

# ---------------------------------------------------------------------------
# Celery Beat — scheduled tasks
# ---------------------------------------------------------------------------
from datetime import timedelta
CELERY_BEAT_SCHEDULE = {
    "aggregate-daily-costs": {
        "task": "apps.costs.tasks.aggregate_daily_costs",
        "schedule": timedelta(hours=1),   # runs every hour
    },
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend"
)

# ---------------------------------------------------------------------------
# Logging — stdout for Render log viewer
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}
