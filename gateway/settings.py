import importlib.util
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency guard
    load_dotenv = None

def _load_env_file(path):
    try:
        with open(path, "r", encoding="utf-8") as env_handle:
            for raw_line in env_handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {"'", '"'}
                ):
                    value = value[1:-1]

                os.environ.setdefault(key, value)
    except FileNotFoundError:
        return


env_file = os.getenv("DJANGO_ENV_FILE", str(BASE_DIR / ".env.local"))
if load_dotenv is not None:
    load_dotenv(env_file, override=False)
else:
    _load_env_file(env_file)

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-change-me",
)

DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"

raw_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,openhim.local")
ALLOWED_HOSTS = [host.strip() for host in raw_hosts.split(",") if host.strip()]
raw_csrf_origins = os.getenv(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:8000,http://localhost:8080,http://localhost:8087,http://openhim.local:8080,http://openhim.local:8087",
)
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in raw_csrf_origins.split(",") if origin.strip()
]

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = []
if importlib.util.find_spec("jazzmin") is not None:
    THIRD_PARTY_APPS.append("jazzmin")

LOCAL_APPS = ["mediator", "notification"]

INSTALLED_APPS = THIRD_PARTY_APPS + DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gateway.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "gateway.wsgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()

if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "openhim"),
            "USER": os.getenv("POSTGRES_USER", "openhim"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "openhim"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5433"),
            "CONN_MAX_AGE": int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5673//")
ENABLE_HEALTH_DEPENDENCY_CHECKS = (
    os.getenv("ENABLE_HEALTH_DEPENDENCY_CHECKS", "false").lower() == "true"
)
MEDIATOR_MODE = os.getenv("MEDIATOR_MODE", "hybrid").strip().lower()
MEDIATOR_FORWARD_TIMEOUT = float(os.getenv("MEDIATOR_FORWARD_TIMEOUT", "30"))
TRANSACTION_BODY_MAX_LENGTH = int(os.getenv("TRANSACTION_BODY_MAX_LENGTH", "20000"))
GOVESB_TOKEN_URL = os.getenv("GOVESB_TOKEN_URL", "")
GOVESB_ENGINE_URL = os.getenv("GOVESB_ENGINE_URL", "")
GOVESB_GRANT_TYPE = os.getenv("GOVESB_GRANT_TYPE", "client_credentials")
ESB_CLIENT_ID = os.getenv("ESB_CLIENT_ID", "")
ESB_CLIENT_SECRET = os.getenv("ESB_CLIENT_SECRET", "")
ESB_REQUEST_DATA_FORMAT = os.getenv("ESB_REQUEST_DATA_FORMAT", "json")
CLIENT_PRIVATE_KEY = os.getenv(
    "CLIENT_PRIVATE_KEY",
    str(BASE_DIR / "esb_utils" / "signatures" / "privateKey.pem"),
)
CLIENT_PUBLIC_KEY = os.getenv(
    "CLIENT_PUBLIC_KEY",
    str(BASE_DIR / "esb_utils" / "signatures" / "publicKey.pem"),
)
GOV_ESB_PUBLIC_KEY = os.getenv("GOV_ESB_PUBLIC_KEY", CLIENT_PUBLIC_KEY)

# Notification transports
NOTIFICATION_SMTP_HOST = os.getenv("NOTIFICATION_SMTP_HOST", "")
NOTIFICATION_SMTP_PORT = int(os.getenv("NOTIFICATION_SMTP_PORT", "587"))
NOTIFICATION_SMTP_USERNAME = os.getenv("NOTIFICATION_SMTP_USERNAME", "")
NOTIFICATION_SMTP_PASSWORD = os.getenv("NOTIFICATION_SMTP_PASSWORD", "")
NOTIFICATION_SMTP_USE_TLS = (
    os.getenv("NOTIFICATION_SMTP_USE_TLS", "true").lower() == "true"
)
NOTIFICATION_SMTP_USE_SSL = (
    os.getenv("NOTIFICATION_SMTP_USE_SSL", "false").lower() == "true"
)
NOTIFICATION_SMTP_TIMEOUT = float(os.getenv("NOTIFICATION_SMTP_TIMEOUT", "15"))
NOTIFICATION_FROM_EMAIL = os.getenv(
    "NOTIFICATION_FROM_EMAIL",
    os.getenv("NOTIFICATION_SMTP_USERNAME", ""),
)

NOTIFICATION_SMS_URL = os.getenv("NOTIFICATION_SMS_URL", "")
NOTIFICATION_SMS_TOKEN = os.getenv("NOTIFICATION_SMS_TOKEN", "")
NOTIFICATION_SMS_AUTH_HEADER = os.getenv("NOTIFICATION_SMS_AUTH_HEADER", "")
NOTIFICATION_SMS_TIMEOUT = float(os.getenv("NOTIFICATION_SMS_TIMEOUT", "15"))
NOTIFICATION_SMS_SENDER_ID = os.getenv("NOTIFICATION_SMS_SENDER_ID", "")
EMBLEM_LOGO_STATIC_PATH = os.getenv("EMBLEM_LOGO_STATIC_PATH", "branding/emblem-logo.png")
EMBLEM_LOGIN_STATIC_PATH = os.getenv(
    "EMBLEM_LOGIN_STATIC_PATH",
    "branding/emblem-login.png",
)
EMBLEM_ICON_STATIC_PATH = os.getenv("EMBLEM_ICON_STATIC_PATH", "branding/emblem-icon.png")
EMBLEM_FAVICON_STATIC_PATH = os.getenv(
    "EMBLEM_FAVICON_STATIC_PATH",
    "branding/emblem-favicon.png",
)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

JAZZMIN_SETTINGS = {
    "site_title": "TTPB Mediator Admin",
    "site_header": "Tanzania Teachers' Professional Board Mediator",
    "site_brand": "TTPB Mediator",
    "site_logo": EMBLEM_LOGO_STATIC_PATH,
    "site_icon": EMBLEM_ICON_STATIC_PATH,
    "login_logo": EMBLEM_LOGIN_STATIC_PATH,
    "login_logo_dark": EMBLEM_LOGIN_STATIC_PATH,
    "welcome_sign": "Tanzania Teachers' Professional Board Mediator",
    "copyright": "TTPB Mediator",
    "show_sidebar": True,
    "navigation_expanded": True,
    "order_with_respect_to": [
        "mediator",
        "mediator.client",
        "mediator.mediator",
        "mediator.channel",
        "mediator.externalsystemregistration",
        "mediator.transaction",
        "notification",
        "notification.lkmodule",
        "notification.lkchannel",
        "notification.lkstatus",
        "notification.lkpriority",
        "notification.lkactortype",
        "notification.eventcatalog",
        "notification.template",
        "notification.userpreference",
        "notification.eventrule",
        "notification.eventrulechannel",
        "notification.outbox",
        "notification.deliverylog",
        "notification.inappmessage",
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "mediator.client": "fas fa-id-card",
        "mediator.mediator": "fas fa-plug",
        "mediator.channel": "fas fa-route",
        "mediator.externalsystemregistration": "fas fa-network-wired",
        "mediator.transaction": "fas fa-exchange-alt",
        "notification.lkmodule": "fas fa-layer-group",
        "notification.lkchannel": "fas fa-broadcast-tower",
        "notification.lkstatus": "fas fa-signal",
        "notification.lkpriority": "fas fa-sort-amount-up",
        "notification.lkactortype": "fas fa-user-tag",
        "notification.eventcatalog": "fas fa-book",
        "notification.template": "fas fa-file-alt",
        "notification.userpreference": "fas fa-sliders-h",
        "notification.eventrule": "fas fa-project-diagram",
        "notification.eventrulechannel": "fas fa-link",
        "notification.outbox": "fas fa-inbox",
        "notification.deliverylog": "fas fa-clipboard-list",
        "notification.inappmessage": "fas fa-bell",
    },
    "custom_links": {
        "mediator": [
            {
                "name": "Integration Tester",
                "url": "admin-mediator-tester",
                "icon": "fas fa-vial",
            }
        ]
    },
    "changeform_format_overrides": {
        "mediator.channel": "horizontal_tabs",
    },
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": None,
    "navbar_small_text": False,
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
}
