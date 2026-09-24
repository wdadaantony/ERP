"""Configuración del ERP.

La base de datos se toma de DATABASE_URL. Si no está definida se usa SQLite,
para que el proyecto arranque sin Postgres instalado durante el desarrollo.
"""
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-inseguro-cambiar-en-produccion")
DEBUG = os.getenv("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

if not DEBUG and SECRET_KEY == "dev-inseguro-cambiar-en-produccion":
    raise RuntimeError("SECRET_KEY debe configurarse fuera de desarrollo")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.terceros",
    "apps.catalogo",
    "apps.inventario",
    "apps.ventas",
    "apps.compras",
    "apps.facturacion",
    "apps.tesoreria",
    "apps.contabilidad",
    "apps.integraciones",
    "apps.crm.apps.CrmConfig",
    "apps.gestion",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.EmpresaActivaMiddleware",
    "apps.core.permisos.PermisosERPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
                "apps.core.context_processors.pendientes",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'dev.sqlite3'}"),
        conn_max_age=600,
    )
}

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"]["OPTIONS"] = {"timeout": 30, "transaction_mode": "IMMEDIATE"}
    if os.getenv("ERP_TEST_DB"):
        DATABASES["default"]["TEST"] = {"NAME": os.environ["ERP_TEST_DB"]}

AUTH_USER_MODEL = "core.Usuario"
AUTHENTICATION_BACKENDS = ['apps.gestion.auth.PermisosPorEmpresa']

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-pe"
TIME_ZONE = "America/Lima"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Detrás de un proxy/balanceador (Render, Railway, Fly, etc.) HTTPS llega como
# cabecera, no como esquema real: sin esto Django cree que todo es HTTP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "1") == "1"
    CSRF_TRUSTED_ORIGINS = [o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
    # En 0 hasta confirmar que el dominio final sirve todo por HTTPS de forma
    # estable: activar HSTS con un dominio que aún falla a HTTPS deja el sitio
    # inaccesible para quien ya recibió la cabecera.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Adaptadores instalados por el operador, nunca clases enviadas desde la web.
import json
ERP_CONNECTOR_BACKENDS = json.loads(os.getenv('ERP_CONNECTOR_BACKENDS', '{}'))
ERP_REQUIRE_ENV_SECRETS = not DEBUG
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '1') == '1'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'ERP <noreply@localhost>')

LOGIN_URL = "cuenta:login"
LOGIN_REDIRECT_URL = "core:inicio"
LOGOUT_REDIRECT_URL = "cuenta:login"

# Sesiones: ocho horas de jornada, y renovación con cada petición.
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
