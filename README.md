# campus-cms

Back office for Campus (Django + DRF). The accountant creates courses, sets
their price (MXN) and, later, sees sales and stats. Open edX keeps the course
content (authored in Studio), users, enrollments and progress; the CMS owns
the product: price, sale status, marketing content, orders and payments.
Both sides are linked by the course key (`course-v1:Org+Number+Run`).

The admin UI lives in Campus (Nuxt, `/admin`); this service only exposes APIs.

## Running it (Tutor dev, from `plataforma-equipo`)

The Tutor plugin in `tutor/campuscms.py` (symlinked into `tutor-plugins/`) adds
the `campus-cms` container to `tutor dev`, with this repo mounted at `/app`, so
code changes reload on their own.

```bash
make update   # first time: enables the plugin, builds the image, runs init
```

`tutor dev do init` (part of `make update`) creates:

- the `campus_cms` MySQL database and user,
- the LMS service account `campus_cms_worker` (staff, no password) and its
  OAuth client-credentials application `campus-cms-service`,
- the CMS tables (`migrate`).

- API: http://campus-cms.local.openedx.io:8010/api/health/
- Check the Open edX connection:
  `make tutor ARGS="dev exec campus-cms python manage.py check_openedx"`

## Settings (environment variables)

| Variable | Default |
|---|---|
| `CAMPUS_CMS_SECRET_KEY` | insecure dev value |
| `CAMPUS_CMS_DEBUG` | `false` |
| `CAMPUS_CMS_ALLOWED_HOSTS` | `localhost,127.0.0.1` |
| `CAMPUS_CMS_MYSQL_HOST` / `_PORT` / `_DATABASE` / `_USERNAME` / `_PASSWORD` | SQLite when no host is set |
| `CAMPUS_CMS_OPENEDX_LMS_URL` | `http://local.openedx.io:8000` |
| `CAMPUS_CMS_OPENEDX_STUDIO_URL` | `http://cms:8000` |
| `CAMPUS_CMS_OAUTH2_CLIENT_ID` / `_SECRET` | service account credentials |

In development the Tutor plugin sets all of them; the secrets are generated
once per machine and stored in `.tutor/config.yml`.
