"""
Tutor plugin: runs the Campus CMS next to Open edX in `tutor dev`.

- Adds the `campus-cms` container (http://campus-cms.local.openedx.io:8010),
  built from this repo and mounted so code changes reload on their own.
- `tutor dev do init` creates its MySQL database, the LMS service account
  (staff user + OAuth client-credentials application) and runs migrations.

Install (single-file plugin):
    ln -s ../src/campus-cms/tutor/campuscms.py tutor-plugins/campuscms.py
"""
import os

from tutor import hooks

# The plugin is usually a symlink: resolve it to find the repo it lives in.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

hooks.Filters.CONFIG_DEFAULTS.add_items(
    [
        ("CAMPUS_CMS_HOST", "campus-cms.local.openedx.io"),
        ("CAMPUS_CMS_PORT", "8010"),
        ("CAMPUS_CMS_MYSQL_DATABASE", "campus_cms"),
        ("CAMPUS_CMS_MYSQL_USERNAME", "campus_cms"),
        ("CAMPUS_CMS_SERVICE_USERNAME", "campus_cms_worker"),
        ("CAMPUS_CMS_OAUTH2_CLIENT_ID", "campus-cms-service"),
        # Stripe keys come from each machine's .env (test keys only), never from the repo.
        ("CAMPUS_CMS_STRIPE_SECRET_KEY", ""),
        ("CAMPUS_CMS_STRIPE_WEBHOOK_SECRET", ""),
        # Public site where Stripe sends buyers back.
        ("CAMPUS_CMS_PUBLIC_SITE_URL", "http://www.local.openedx.io:3000"),
    ]
)

hooks.Filters.CONFIG_UNIQUE.add_items(
    [
        ("CAMPUS_CMS_SECRET_KEY", "{{ 32|random_string }}"),
        ("CAMPUS_CMS_MYSQL_PASSWORD", "{{ 16|random_string }}"),
        ("CAMPUS_CMS_OAUTH2_CLIENT_SECRET", "{{ 32|random_string }}"),
    ]
)

_ENVIRONMENT = """
{% set jwt_rsa_key | rsa_import_key %}{{ JWT_RSA_PRIVATE_KEY }}{% endset %}
  environment:
    CAMPUS_CMS_DEBUG: "true"
    CAMPUS_CMS_SECRET_KEY: "{{ CAMPUS_CMS_SECRET_KEY }}"
    CAMPUS_CMS_ALLOWED_HOSTS: "{{ CAMPUS_CMS_HOST }},campus-cms,localhost,127.0.0.1"
    CAMPUS_CMS_MYSQL_HOST: "{{ MYSQL_HOST }}"
    CAMPUS_CMS_MYSQL_PORT: "{{ MYSQL_PORT }}"
    CAMPUS_CMS_MYSQL_DATABASE: "{{ CAMPUS_CMS_MYSQL_DATABASE }}"
    CAMPUS_CMS_MYSQL_USERNAME: "{{ CAMPUS_CMS_MYSQL_USERNAME }}"
    CAMPUS_CMS_MYSQL_PASSWORD: "{{ CAMPUS_CMS_MYSQL_PASSWORD }}"
    # "local.openedx.io" is an alias of the lms container in the dev network.
    CAMPUS_CMS_OPENEDX_LMS_URL: "http://{{ LMS_HOST }}:8000"
    CAMPUS_CMS_OPENEDX_STUDIO_URL: "http://cms:8000"
    CAMPUS_CMS_OAUTH2_CLIENT_ID: "{{ CAMPUS_CMS_OAUTH2_CLIENT_ID }}"
    CAMPUS_CMS_OAUTH2_CLIENT_SECRET: "{{ CAMPUS_CMS_OAUTH2_CLIENT_SECRET }}"
    # Public half of the key the LMS signs login JWTs with (same as the LMS settings).
    CAMPUS_CMS_OPENEDX_JWT_PUBLIC_JWK_SET: '{"keys": [{"kid": "openedx", "kty": "RSA", "e": "{{ jwt_rsa_key.e|long_to_base64 }}", "n": "{{ jwt_rsa_key.n|long_to_base64 }}"}]}'
    CAMPUS_CMS_OPENEDX_JWT_ISSUER: "{{ JWT_COMMON_ISSUER }}"
    CAMPUS_CMS_OPENEDX_JWT_AUDIENCE: "{{ JWT_COMMON_AUDIENCE }}"
    CAMPUS_CMS_STRIPE_SECRET_KEY: "{{ CAMPUS_CMS_STRIPE_SECRET_KEY }}"
    CAMPUS_CMS_STRIPE_WEBHOOK_SECRET: "{{ CAMPUS_CMS_STRIPE_WEBHOOK_SECRET }}"
    CAMPUS_CMS_PUBLIC_SITE_URL: "{{ CAMPUS_CMS_PUBLIC_SITE_URL }}"
  volumes:
    - "REPO:/app"
  depends_on: {{ [("mysql", RUN_MYSQL)]|list_if }}
""".replace("REPO", REPO)

hooks.Filters.ENV_PATCHES.add_items(
    [
        (
            "local-docker-compose-dev-services",
            f"""
campus-cms:
  image: campus-cms-dev
  build:
    context: "{REPO}"
  command: python manage.py runserver 0.0.0.0:8000
  ports:
    - "{{{{ CAMPUS_CMS_PORT }}}}:8000"
  restart: unless-stopped
{_ENVIRONMENT}""",
        ),
        (
            "dev-docker-compose-jobs-services",
            f"""
campus-cms-job:
  image: campus-cms-dev
  build:
    context: "{REPO}"
{_ENVIRONMENT}""",
        ),
    ]
)

_MYSQL_INIT = """
mysql -u {{ MYSQL_ROOT_USERNAME }} --password="{{ MYSQL_ROOT_PASSWORD }}" --host "{{ MYSQL_HOST }}" --port {{ MYSQL_PORT }} -e "CREATE DATABASE IF NOT EXISTS {{ CAMPUS_CMS_MYSQL_DATABASE }} CHARACTER SET utf8mb4;"
mysql -u {{ MYSQL_ROOT_USERNAME }} --password="{{ MYSQL_ROOT_PASSWORD }}" --host "{{ MYSQL_HOST }}" --port {{ MYSQL_PORT }} -e "CREATE USER IF NOT EXISTS '{{ CAMPUS_CMS_MYSQL_USERNAME }}';"
mysql -u {{ MYSQL_ROOT_USERNAME }} --password="{{ MYSQL_ROOT_PASSWORD }}" --host "{{ MYSQL_HOST }}" --port {{ MYSQL_PORT }} -e "ALTER USER '{{ CAMPUS_CMS_MYSQL_USERNAME }}'@'%' IDENTIFIED BY '{{ CAMPUS_CMS_MYSQL_PASSWORD }}';"
mysql -u {{ MYSQL_ROOT_USERNAME }} --password="{{ MYSQL_ROOT_PASSWORD }}" --host "{{ MYSQL_HOST }}" --port {{ MYSQL_PORT }} -e "GRANT ALL ON {{ CAMPUS_CMS_MYSQL_DATABASE }}.* TO '{{ CAMPUS_CMS_MYSQL_USERNAME }}'@'%';"
# Django's test runner creates and drops test_<database>.
mysql -u {{ MYSQL_ROOT_USERNAME }} --password="{{ MYSQL_ROOT_PASSWORD }}" --host "{{ MYSQL_HOST }}" --port {{ MYSQL_PORT }} -e "GRANT ALL ON test_{{ CAMPUS_CMS_MYSQL_DATABASE }}.* TO '{{ CAMPUS_CMS_MYSQL_USERNAME }}'@'%';"
"""

# Service account: a staff user (Studio's course run API requires is_staff)
# that owns a client-credentials application. No password: it only logs in
# through the OAuth client.
_LMS_INIT = """
./manage.py lms manage_user {{ CAMPUS_CMS_SERVICE_USERNAME }} {{ CAMPUS_CMS_SERVICE_USERNAME }}@openedx --staff --unusable-password
./manage.py lms create_dot_application \\
  --grant-type client-credentials \\
  --client-id {{ CAMPUS_CMS_OAUTH2_CLIENT_ID }} \\
  --client-secret {{ CAMPUS_CMS_OAUTH2_CLIENT_SECRET }} \\
  --update campus-cms-service {{ CAMPUS_CMS_SERVICE_USERNAME }}
"""

hooks.Filters.CLI_DO_INIT_TASKS.add_items(
    [
        ("mysql", _MYSQL_INIT),
        ("lms", _LMS_INIT),
        ("campus-cms", "python manage.py migrate --noinput"),
    ]
)
