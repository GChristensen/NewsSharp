"""Django settings for NewsSharp (config/settings.py). Shared by the web process, the worker coordinator
and the pool children (R23.3)."""
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- .env keys (R23.1) ---
PORT = _int("PORT", 8000)
BIND = os.environ.get("BIND", "0.0.0.0")
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = (BASE_DIR / DATA_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"
FETCH_INTERVAL_MINUTES = _int("FETCH_INTERVAL_MINUTES", 60)
STORIES_PER_FEED = _int("STORIES_PER_FEED", 500)
WORKER_PROCESSES = max(1, _int("WORKER_PROCESSES", 2))
PUSH_LEASE_SECONDS = _int("PUSH_LEASE_SECONDS", 2147483647)
IMAGE_CACHE_MB = _int("IMAGE_CACHE_MB", 0)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()

APP_NAME = "NewsSharp"
APP_VERSION = "0.1.0"

_secret_file = DATA_DIR / "secret_key"
if not _secret_file.exists():
    _secret_file.write_text(secrets.token_urlsafe(48), encoding="ascii")
SECRET_KEY = _secret_file.read_text(encoding="ascii").strip()

DEBUG = False
ALLOWED_HOSTS = ["*"]  # R0.34: no host name is configured

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "feeds",
    "reading",
    "worker",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "api.middleware.app_middleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --- SQLite (R23.3, R0.18, R0.35) ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "newssharp.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 5,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;",
        },
    }
}
ATOMIC_REQUESTS = False
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- accounts and sessions (item 22, R5.5, R5.6) ---
AUTH_USER_MODEL = "accounts.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = []
MAX_USERS = 10
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 90 * 24 * 3600
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = []
CSRF_FAILURE_VIEW = "api.http.csrf_failure"
LOGIN_URL = "/login"

if TRUST_PROXY:  # R5.3
    USE_X_FORWARDED_HOST = True
    USE_X_FORWARDED_PORT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

USE_TZ = True
TIME_ZONE = "UTC"
USE_I18N = False

# --- static files: the built frontend is served by whitenoise (R20.8) ---
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
STATIC_ROOT.mkdir(exist_ok=True)
WHITENOISE_ROOT = FRONTEND_DIST if FRONTEND_DIST.exists() else None
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = False
WHITENOISE_INDEX_FILE = False

DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024  # pushed bodies up to 10 MB (R4.6)
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024


# --- logging to stdout, prefixed by process (R23.6) ---
class ProcFilter(logging.Filter):
    def filter(self, record):
        record.proc = os.environ.get("NEWSSHARP_PROC", "main")
        return True


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"proc": {"()": ProcFilter}},
    "formatters": {"line": {"format": "%(asctime)s %(proc)s %(levelname)s %(message)s", "datefmt": "%H:%M:%S"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "line",
            "filters": ["proc"],
        }
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL if LOG_LEVEL in logging._nameToLevel else "INFO"},
    "loggers": {
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
        "trafilatura": {"level": "ERROR"},
        "readability": {"level": "ERROR"},
        "htmldate": {"level": "ERROR"},
        "courlan": {"level": "ERROR"},
        "PIL": {"level": "WARNING"},
        "asyncio": {"level": "WARNING"},
    },
}
