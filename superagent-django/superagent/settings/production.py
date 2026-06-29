"""
Production settings for Railway deployment.

Railway injects these env vars automatically:
  DATABASE_URL  — Postgres connection string
  REDIS_URL     — Redis connection string (add a Redis service in Railway)
  PORT          — Port gunicorn should bind to

You must set these manually in Railway → Variables:
  SECRET_KEY
  ALLOWED_HOSTS          e.g. yourapp.railway.app
  CORS_ALLOWED_ORIGINS   e.g. https://yourmobileapp.com
  GROQ_API_KEY
"""

from .base import *  # noqa: F401,F403
import os

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*.railway.app"])

SECRET_KEY = env("SECRET_KEY")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
# Database — Railway injects DATABASE_URL automatically
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL")
}

# ---------------------------------------------------------------------------
# Static files — WhiteNoise serves them directly from gunicorn
# ---------------------------------------------------------------------------
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Redis — Celery broker + Django Channels
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = False   # real async workers in production

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    }
}

# ---------------------------------------------------------------------------
# CORS — lock down to your mobile app origin
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Email — set EMAIL_BACKEND=smtp in Railway vars to enable real sending
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend"
)

# ---------------------------------------------------------------------------
# Logging — print to stdout so Railway captures it
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}
