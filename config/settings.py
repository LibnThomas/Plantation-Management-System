"""
Plantation Management System - Django settings.

All secrets / environment-specific values are read from environment variables
(or a `.env` file in the project root). See `.env.example`.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so we don't need an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return str(env(key, default)).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", True)  # set DJANGO_DEBUG=False on a server
_DEV_KEY = "dev-insecure-change-me-in-production"
SECRET_KEY = env("DJANGO_SECRET_KEY", _DEV_KEY)
if not DEBUG and SECRET_KEY == _DEV_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY (a long random string) when DJANGO_DEBUG is False.")
ALLOWED_HOSTS = [
    h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*" if DEBUG else "localhost,127.0.0.1").split(",") if h.strip()
]
CSRF_TRUSTED_ORIGINS = [h.strip() for h in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "estate",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.PMSMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.AccessMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "estate.context_processors.app_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Databases
#   default : the estate's real data (PostgreSQL; DB_ENGINE=sqlite for a quick local try)
#   demo    : sample data for "Demo mode" - a separate database, so real data is never touched
# ---------------------------------------------------------------------------
if env("DB_ENGINE", "postgres").lower() == "sqlite":
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))},
        "demo": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("SQLITE_DEMO_PATH", str(BASE_DIR / "db_demo.sqlite3")),
        },
    }
else:
    _pg = {
        "ENGINE": "django.db.backends.postgresql",
        "USER": env("DB_USER", "postgres"),
        "PASSWORD": env("DB_PASSWORD", "postgres"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
    DATABASES = {
        "default": {**_pg, "NAME": env("DB_NAME", "plantation_db")},
        "demo": {**_pg, "NAME": env("DEMO_DB_NAME", env("DB_NAME", "plantation_db") + "_demo")},
    }
DATABASE_ROUTERS = ["core.dbrouter.PMSRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("ml", "Malayalam"), ("hi", "Hindi"), ("ta", "Tamil")]
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
FORMAT_MODULE_PATH = ["config.formats"]  # DD/MM/YY dates in every language
USE_TZ = True
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Display settings
ESTATE_NAME = env("ESTATE_NAME", "My Cardamom Estate")
CURRENCY_SYMBOL = env("CURRENCY_SYMBOL", "₹")
# First month of the financial year and of the crop season (1 = January)
FINANCIAL_YEAR_START_MONTH = int(env("FINANCIAL_YEAR_START_MONTH", "4"))
CROP_SEASON_START_MONTH = int(env("CROP_SEASON_START_MONTH", "8"))
# Automatic numbers: EMP0001 for employees, PB2610-0001 for processing batches
EMPLOYEE_CODE_PREFIX = env("EMPLOYEE_CODE_PREFIX", "EMP")
BATCH_NO_PREFIX = env("BATCH_NO_PREFIX", "PB")
# ESTATE_NAME ... BATCH_NO_PREFIX above are only the starting values of App Settings (copied once by
# the migration); after that the administrator changes them in App Settings.

# Subscription (see core/licence.py)
LICENCE_TRIAL_DAYS = 30
LICENCE_WARN_DAYS = 7  # banner this many days before the end
LICENCE_GRACE_DAYS = 7  # full access this many days after the end, then read-only
LICENCE_SUPPORT_CONTACT = env("LICENCE_SUPPORT_CONTACT", "Mentors Soft Tech")
# False: the administrator cannot type the valid till date or switch the API off (vendor-controlled installs)
SUBSCRIPTION_MANUAL_ALLOWED = env_bool("SUBSCRIPTION_MANUAL_ALLOWED", True)
SUBSCRIPTION_API_URL = env("SUBSCRIPTION_API_URL", "")  # used when App Settings has no URL (or when locked)
SUBSCRIPTION_API_TOKEN = env("SUBSCRIPTION_API_TOKEN", "")
SUBSCRIPTION_API_TIMEOUT = 8  # seconds
PWA_VERSION = "31"  # raise to make installed apps fetch new styles / scripts
# "Add new" pop-ups show the app's own forms in a frame; other sites still can't frame the app
X_FRAME_OPTIONS = "SAMEORIGIN"
SUBSCRIPTION_API_SYNC = False  # True: check in the request itself instead of a background thread (tests)

AUTHENTICATION_BACKENDS = ["core.auth.PlanUserLimitBackend"]

# ---------------------------------------------------------------------------
# Phone app (mobile/ - Android / iPhone): the whole app runs on the phone, offline, on its own SQLite
# database. mobile/src/pms_mobile/launcher.py sets these variables before Django starts.
# ---------------------------------------------------------------------------
MOBILE_APP = env_bool("PMS_MOBILE", False)
MOBILE_PLATFORM = env("PMS_MOBILE_PLATFORM", "")  # android / ios
MOBILE_DEFAULT_USER = "admin"
MOBILE_DEFAULT_PASSWORD = "admin1234"  # shown on the login page until the admin has logged in once
if MOBILE_APP:
    for _db in DATABASES.values():
        # one writer at a time, readers never blocked: safe for the app's few threads
        _db["OPTIONS"] = {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;",
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        }
    SECURE_COOKIES_DEFAULT = False  # http://127.0.0.1 inside the phone: no https
    SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
    # phones are slower than servers: fewer password-hash rounds (still salted PBKDF2-SHA256)
    PASSWORD_HASHERS = ["core.hashers.MobilePBKDF2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(Path(env("PMS_DATA_DIR", BASE_DIR)) / "pms-errors.log"),
                "maxBytes": 512 * 1024,
                "backupCount": 1,
                "level": "ERROR",
            }
        },
        "loggers": {"django": {"handlers": ["file"], "level": "ERROR"}},
    }
else:
    SECURE_COOKIES_DEFAULT = True

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", SECURE_COOKIES_DEFAULT)
    CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", SECURE_COOKIES_DEFAULT)
