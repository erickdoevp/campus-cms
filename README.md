# campus-cms

Back office for Campus (Django + DRF). The accountant creates courses, sets
their price (MXN) and, later, sees sales and stats. It also sells them: Stripe
checkout, webhooks, orders, and the course access in Open edX that comes with
a payment. Open edX keeps the course
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
| GET | `/api/v1/catalog/` | anyone | Courses on sale, for the public site: `{id (course key), title, org, number, short_description, description, image, price, currency, on_sale}` |
| GET | `/api/v1/catalog/{course_key}/` | anyone | One of them; archived courses are found too (`on_sale: false`), drafts and created ones aren't |
| POST | `/api/v1/checkout/` | logged in | `{"course_id"}` → `{"order_id", "checkout_url"}`; errors `{"code": "course_not_found" \| "not_purchasable" \| "already_owned" \| "payment_unavailable"}` |
| GET | `/api/v1/me/orders/` | logged in | The user's orders |
| POST | `/api/v1/webhooks/stripe/` | Stripe signature | Stripe events |
| GET, POST | `/api/v1/products/` | contador | List (`?status=`) / create a product (JSON) |
| GET, PATCH, PUT, DELETE | `/api/v1/products/{id}/` | contador | Read / edit / delete (only before it exists in Open edX) |
| POST | `/api/v1/products/{id}/image/` | contador | Upload the image, multipart field `image` (JPG/PNG, 5 MB max) |
| POST | `/api/v1/products/{id}/create-course/` | contador | Draft → created: creates the course in Open edX |
| POST | `/api/v1/products/{id}/put-on-sale/` | contador | Created → on sale (needs price and image) |
| POST | `/api/v1/products/{id}/withdraw/` | contador | On sale → created |
| POST | `/api/v1/products/{id}/archive/` | contador | Created or on sale → archived |

A product is a course for sale: title, code (`number`) and period (`run`) that
form the course key `course-v1:{org}+{number}+{run}` (`org` comes from
`CAMPUS_CMS_COURSE_ORG`), price in MXN, descriptions, image, dates, pacing and
`authors` (Open edX usernames, checked against the LMS, that will get the
instructor role).

## Lifecycle and what goes to Open edX

```
draft ──create-course──► created ──put-on-sale──► on_sale
                            ▲  ◄──────withdraw──────┘
                            └──────► archived ◄─────┘ (archive)
```

`catalog/openedx_sync.py` makes every call as the service account:

- **create-course** (needs a start date and at least one author): Studio course
  run with title, pacing and the authors as `instructor`, then start/end dates
  (course details API) and the card image. The course key is saved as soon as
  the course exists and the status changes only at the end, so a `draft` with
  a `course_key` is a creation that failed half way: calling create-course
  again adopts the existing course and finishes it.
- **Image**: uploading a new one also replaces it in Studio.
- **Access mode**: create-course also creates the `verified` course mode,
  which buyers are enrolled in. It is never removed (Open edX requires
  `min_price` >= 1 on it; the value isn't used).
- **Sale**: price (centavos allowed) and put-on-sale / withdraw / archive only
  change the CMS. Buyers keep their access whatever the status.

Once the course exists in Open edX its key (`number`, `run`), dates, pacing and
team are managed in Studio and can't be changed here; title, descriptions,
price and image stay in the CMS. It can only be archived, not deleted.

## Sales

1. Campus' `/comprar/{course_id}` calls `POST /api/v1/checkout/` as the user: a
   `pending` Order and a Stripe Checkout Session (price in centavos, MXN). An
   open session for the same user, product and price is reused, so two tabs
   don't make two payable checkouts.
2. Stripe calls `POST /api/v1/webhooks/stripe/` (verified with the signing
   secret, deduplicated by event id in `PaymentEvent`):
   - `checkout.session.completed` (card, paid) → Order `paid`, then access.
   - OXXO: `completed` arrives unpaid; `checkout.session.async_payment_succeeded` pays it.
   - `checkout.session.expired` / `async_payment_failed` → Order `failed`.
   - `charge.refunded` (full) → Order `refunded`, access removed. Partial refunds keep it.
   - A second payment for a course already paid is refunded automatically.
3. **Access** is a separate field (`Order.access`): after the payment is saved,
   the CMS enrolls the buyer as `verified` through the LMS enrollment API (and
   creates the course mode if missing). If Open edX fails, Stripe still gets a
   200, the order stays `paid` with access `error`, and `reconcile_orders`
   retries it.

`reconcile_orders` (run it daily, and after an Open edX outage) retries access
for paid/refunded orders, settles with Stripe pending orders older than 24 h
whose webhook never came, and reports active `verified` enrollments with no
paid order:

```bash
make tutor ARGS="dev exec campus-cms python manage.py reconcile_orders"
```

Stripe test webhooks reach the local CMS with `make stripe` (from `plataforma-equipo`).

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
| `CAMPUS_CMS_STRIPE_SECRET_KEY` / `_WEBHOOK_SECRET` | `""` (from each machine's `.env`; test keys only) |
| `CAMPUS_CMS_PUBLIC_SITE_URL` | `http://www.local.openedx.io:3000` (Stripe returns buyers to `/cursos/{course_key}`) |

In development the Tutor plugin sets all of them; the secrets are generated
once per machine and stored in `.tutor/config.yml`.
