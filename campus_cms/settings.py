"""
Settings for the Campus CMS. Every value that changes per environment comes
from an environment variable; in development the Tutor plugin
(tutor/campuscms.py) sets them on the container.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes")


SECRET_KEY = env("CAMPUS_CMS_SECRET_KEY", "insecure-dev-only")
DEBUG = env_bool("CAMPUS_CMS_DEBUG")
ALLOWED_HOSTS = [h for h in env("CAMPUS_CMS_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "campus_cms.urls"
WSGI_APPLICATION = "campus_cms.wsgi.application"

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
            ],
        },
    },
]

# MySQL from the Tutor stack, in its own database. Without CAMPUS_CMS_MYSQL_HOST
# (e.g. running outside Docker) a local SQLite file is used.
if env("CAMPUS_CMS_MYSQL_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "HOST": env("CAMPUS_CMS_MYSQL_HOST"),
            "PORT": env("CAMPUS_CMS_MYSQL_PORT", "3306"),
            "NAME": env("CAMPUS_CMS_MYSQL_DATABASE", "campus_cms"),
            "USER": env("CAMPUS_CMS_MYSQL_USERNAME", "campus_cms"),
            "PASSWORD": env("CAMPUS_CMS_MYSQL_PASSWORD"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# The API only takes the Open edX JWT in the Authorization header (Campus'
# server builds it from the LMS cookies). No session auth, so no CSRF.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["core.authentication.OpenEdxJwtAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# Open edX, reached from inside the Docker network. The service account
# (an OAuth client-credentials application owned by a staff user in the LMS)
# is used for every call the CMS makes on its own behalf.
OPENEDX_LMS_URL = env("CAMPUS_CMS_OPENEDX_LMS_URL", "http://local.openedx.io:8000").rstrip("/")
OPENEDX_STUDIO_URL = env("CAMPUS_CMS_OPENEDX_STUDIO_URL", "http://cms:8000").rstrip("/")
OPENEDX_OAUTH2_CLIENT_ID = env("CAMPUS_CMS_OAUTH2_CLIENT_ID")
OPENEDX_OAUTH2_CLIENT_SECRET = env("CAMPUS_CMS_OAUTH2_CLIENT_SECRET")

# Users log in to the LMS; the CMS validates the JWT the LMS puts in its
# cookies (signed with the LMS private key, verified with this public set).
OPENEDX_JWT_PUBLIC_JWK_SET = env("CAMPUS_CMS_OPENEDX_JWT_PUBLIC_JWK_SET", '{"keys": []}')
OPENEDX_JWT_ISSUER = env("CAMPUS_CMS_OPENEDX_JWT_ISSUER", "http://local.openedx.io/oauth2")
OPENEDX_JWT_AUDIENCE = env("CAMPUS_CMS_OPENEDX_JWT_AUDIENCE", "openedx")

# Organization of every course the CMS creates (first part of the course key).
COURSE_ORG = env("CAMPUS_CMS_COURSE_ORG", "Campus")
