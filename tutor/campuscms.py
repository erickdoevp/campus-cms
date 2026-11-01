"""
Tutor plugin: runs the Campus CMS next to Open edX.

- `tutor dev`: adds the `campus-cms` container
  (http://campus-cms.local.openedx.io:8010), built from this repo and mounted
  so code changes reload on their own.
- `tutor local` (QA/production): runs the image from the registry
  (`CAMPUS_CMS_DOCKER_IMAGE`) with gunicorn, behind Caddy at
  `CAMPUS_CMS_HOST` (Stripe webhooks and the Django admin). Uploaded images
  are kept in `$(tutor config printroot)/data/campus-cms/media`.
- `tutor images build/push/pull campus-cms` builds this repo's Dockerfile and
  moves the image through the registry.
- `tutor dev|local do init` creates its MySQL database, the LMS service account
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
        ("CAMPUS_CMS_DOCKER_IMAGE", "campus-cms:latest"),
        ("CAMPUS_CMS_GUNICORN_WORKERS", 3),
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


def _environment(dev: bool) -> str:
    """
    Container settings. Both variants set the same keys: `tutor dev` merges the
    dev job over the local one.
    """
    # Development: "local.openedx.io" is an alias of the lms container in the dev
    # network. Local: straight to the containers, without going out through Caddy.
    lms_url = "http://{{ LMS_HOST }}:8000" if dev else "http://lms:8000"
    public_url = (
        "http://{{ CAMPUS_CMS_HOST }}:{{ CAMPUS_CMS_PORT }}"
        if dev
        else "{% if ENABLE_HTTPS %}https{% else %}http{% endif %}://{{ CAMPUS_CMS_HOST }}"
    )
    return f"""
{{% set jwt_rsa_key | rsa_import_key %}}{{{{ JWT_RSA_PRIVATE_KEY }}}}{{% endset %}}
  environment:
    CAMPUS_CMS_DEBUG: "{"true" if dev else "false"}"
    CAMPUS_CMS_SECRET_KEY: "{{{{ CAMPUS_CMS_SECRET_KEY }}}}"
    CAMPUS_CMS_ALLOWED_HOSTS: "{{{{ CAMPUS_CMS_HOST }}}},campus-cms,localhost,127.0.0.1"
    CAMPUS_CMS_CSRF_TRUSTED_ORIGINS: "{public_url}"
    # Uploaded images: absolute URLs, so they work wherever the API is called from.
    CAMPUS_CMS_MEDIA_URL: "{public_url}/media/"
    CAMPUS_CMS_MYSQL_HOST: "{{{{ MYSQL_HOST }}}}"
    CAMPUS_CMS_MYSQL_PORT: "{{{{ MYSQL_PORT }}}}"
    CAMPUS_CMS_MYSQL_DATABASE: "{{{{ CAMPUS_CMS_MYSQL_DATABASE }}}}"
    CAMPUS_CMS_MYSQL_USERNAME: "{{{{ CAMPUS_CMS_MYSQL_USERNAME }}}}"
    CAMPUS_CMS_MYSQL_PASSWORD: "{{{{ CAMPUS_CMS_MYSQL_PASSWORD }}}}"
    CAMPUS_CMS_OPENEDX_LMS_URL: "{lms_url}"
    CAMPUS_CMS_OPENEDX_STUDIO_URL: "http://cms:8000"
    CAMPUS_CMS_OAUTH2_CLIENT_ID: "{{{{ CAMPUS_CMS_OAUTH2_CLIENT_ID }}}}"
    CAMPUS_CMS_OAUTH2_CLIENT_SECRET: "{{{{ CAMPUS_CMS_OAUTH2_CLIENT_SECRET }}}}"
    # Public half of the key the LMS signs login JWTs with (same as the LMS settings).
    CAMPUS_CMS_OPENEDX_JWT_PUBLIC_JWK_SET: '{{"keys": [{{"kid": "openedx", "kty": "RSA", "e": "{{{{ jwt_rsa_key.e|long_to_base64 }}}}", "n": "{{{{ jwt_rsa_key.n|long_to_base64 }}}}"}}]}}'
    CAMPUS_CMS_OPENEDX_JWT_ISSUER: "{{{{ JWT_COMMON_ISSUER }}}}"
    CAMPUS_CMS_OPENEDX_JWT_AUDIENCE: "{{{{ JWT_COMMON_AUDIENCE }}}}"
    CAMPUS_CMS_STRIPE_SECRET_KEY: "{{{{ CAMPUS_CMS_STRIPE_SECRET_KEY }}}}"
    CAMPUS_CMS_STRIPE_WEBHOOK_SECRET: "{{{{ CAMPUS_CMS_STRIPE_WEBHOOK_SECRET }}}}"
    CAMPUS_CMS_PUBLIC_SITE_URL: "{{{{ CAMPUS_CMS_PUBLIC_SITE_URL }}}}"
  volumes:
    - {f'"{REPO}:/app"' if dev else "../../data/campus-cms/media:/app/media"}
  depends_on: {{{{ [("mysql", RUN_MYSQL)]|list_if }}}}
"""


hooks.Filters.ENV_PATCHES.add_items(
    [
        (
            "local-docker-compose-dev-services",
            f"""
campus-cms:
  image: campus-cms-dev
  build:
    context: "{REPO}"
  # Wait for MySQL first: if runserver starts while MySQL is still booting
  # (e.g. everything restarted together), it fails its checks and then keeps
  # running without serving anything until a file changes.
  command: >-
    sh -c "until python manage.py check --database default >/dev/null 2>&1;
    do echo 'Esperando a MySQL...'; sleep 2; done;
    exec python manage.py runserver 0.0.0.0:8000"
  ports:
    - "{{{{ CAMPUS_CMS_PORT }}}}:8000"
  restart: unless-stopped
{_environment(dev=True)}""",
        ),
        (
            "dev-docker-compose-jobs-services",
            f"""
campus-cms-job:
  image: campus-cms-dev
  build:
    context: "{REPO}"
{_environment(dev=True)}""",
        ),
        # Only `tutor local` reads the prod file; Caddy publishes it.
        (
            "local-docker-compose-prod-services",
            f"""
campus-cms:
  image: {{{{ CAMPUS_CMS_DOCKER_IMAGE }}}}
  command: >-
    sh -c "until python manage.py check --database default >/dev/null 2>&1;
    do echo 'Esperando a MySQL...'; sleep 2; done;
    exec gunicorn campus_cms.wsgi --bind 0.0.0.0:8000
    --workers {{{{ CAMPUS_CMS_GUNICORN_WORKERS }}}} --timeout 150 --access-logfile -"
  restart: unless-stopped
{_environment(dev=False)}""",
        ),
        (
            "local-docker-compose-jobs-services",
            f"""
campus-cms-job:
  image: {{{{ CAMPUS_CMS_DOCKER_IMAGE }}}}
{_environment(dev=False)}""",
        ),
        (
            "caddyfile",
            """
# Campus CMS: Stripe webhooks and the Django admin. Campus calls its API from inside the network.
{{ CAMPUS_CMS_HOST }}{$default_site_port} {
    import proxy "campus-cms:8000"
}
""",
        ),
    ]
)

hooks.Filters.IMAGES_BUILD.add_item(
    # An absolute path: the build context is this repo, not Tutor's env.
    ("campus-cms", REPO, "{{ CAMPUS_CMS_DOCKER_IMAGE }}", ())
)
hooks.Filters.IMAGES_PULL.add_item(("campus-cms", "{{ CAMPUS_CMS_DOCKER_IMAGE }}"))
hooks.Filters.IMAGES_PUSH.add_item(("campus-cms", "{{ CAMPUS_CMS_DOCKER_IMAGE }}"))

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
