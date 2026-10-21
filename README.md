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
- the CMS tables and the Contadores group (`migrate`).

- API: http://campus-cms.local.openedx.io:8010/api/health/
- Check the Open edX connection:
  `make tutor ARGS="dev exec campus-cms python manage.py check_openedx"`

## Who can do what

Nobody has a CMS password. People log in to the LMS, and the LMS JWT it keeps
in the `edx-jwt-cookie-header-payload` + `edx-jwt-cookie-signature` cookies
(shared domain) is the API credential: Campus' server joins the two cookies and
calls the CMS with `Authorization: JWT <header.payload.signature>`. The CMS
checks the signature with the LMS public key, the issuer and the audience, and
creates/updates the local user linked by the LMS `user_id` (`LmsAccount`).
Session auth is off for the API, so there is no CSRF to deal with.

Only members of the **Contadores** group (or superusers) can use the product
API. Grant or remove the role by Open edX username, even before their first
login:

```bash
make tutor ARGS="dev exec campus-cms python manage.py contador <username>"
make tutor ARGS="dev exec campus-cms python manage.py contador <username> --remove"
```

## API

| Method | URL | Who | Description |
|---|---|---|---|
| GET | `/api/health/` | anyone | Service and database are up |
| GET | `/api/v1/me/` | logged in | `{username, email, is_contador}` |
| GET, POST | `/api/v1/products/` | contador | List (`?status=`) / create a product (JSON) |
| GET, PATCH, PUT, DELETE | `/api/v1/products/{id}/` | contador | Read / edit / delete (only before it exists in Open edX) |
| POST | `/api/v1/products/{id}/image/` | contador | Upload the image, multipart field `image` (JPG/PNG, 5 MB max) |

A product is a course for sale: title, code (`number`) and period (`run`) that
form the course key `course-v1:{org}+{number}+{run}` (`org` comes from
`CAMPUS_CMS_COURSE_ORG`), price in MXN, descriptions, image, dates, pacing and
`authors` (Open edX usernames, checked against the LMS, that will get the
instructor role). It starts as `draft`; creating it in Open edX and putting it
on sale come next. Once it has a `course_key`, `number` and `run` are locked
and it can only be archived, not deleted.

## Tests

```bash
make tutor ARGS="dev exec campus-cms python manage.py test"
```

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
| `CAMPUS_CMS_OPENEDX_JWT_PUBLIC_JWK_SET` | `{"keys": []}` (no logins) |
| `CAMPUS_CMS_OPENEDX_JWT_ISSUER` | `http://local.openedx.io/oauth2` |
| `CAMPUS_CMS_OPENEDX_JWT_AUDIENCE` | `openedx` |
| `CAMPUS_CMS_COURSE_ORG` | `Campus` |

In development the Tutor plugin sets all of them; the secrets are generated
once per machine and stored in `.tutor/config.yml`.
