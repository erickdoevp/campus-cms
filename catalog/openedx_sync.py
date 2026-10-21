"""
Writes a product to Open edX, as the CMS service account.

What the CMS sends, and when:
- Creating the course (once): Studio course run with title, pacing and the
  authors as instructors, then the dates and the card image.
- The image, again whenever it changes.
- The sale, mirrored into the ``verified`` course mode while the payments
  plugin in the LMS still does the charging: the mode exists, with the price
  as ``min_price``, exactly while the product is on sale. Without it the
  plugin doesn't sell the course (and Open edX refuses a verified mode at 0).
  Learners who already bought keep their ``verified`` enrollment.

After creation, dates, pacing and the course team belong to Studio.
Every step can be repeated safely, so a failed sync is fixed by retrying.
"""
import json
import logging

from core import openedx

from .models import Product

log = logging.getLogger(__name__)

AUTHOR_ROLE = "instructor"
IMAGE_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class SyncError(Exception):
    """Open edX rejected or didn't answer a call; the message is safe to show."""


def _call(step, func, method, path, **kwargs):
    try:
        return func(method, path, **kwargs)
    except openedx.OpenEdxError as exc:
        log.warning("Open edX sync failed (%s): %s", step, exc)
        raise SyncError(f"Open edX no aceptó el paso «{step}». Intenta de nuevo en un momento.") from exc


def _course_exists(course_key):
    try:
        openedx.studio("GET", f"/api/v1/course_runs/{course_key}/")
    except openedx.OpenEdxError as exc:
        if exc.status_code == 404:
            return False
        raise SyncError("No se pudo consultar Studio. Intenta de nuevo en un momento.") from exc
    return True


def ensure_course(product):
    """Create the course run in Studio, or adopt it if a previous try already did."""
    course_key = product.course_key or product.planned_course_key
    team = [{"user": username, "role": AUTHOR_ROLE} for username in product.authors]
    if _course_exists(course_key):
        # Partial update: adds the authors without removing anyone.
        _call("equipo del curso", openedx.studio, "PATCH", f"/api/v1/course_runs/{course_key}/", json={"team": team})
    else:
        _call("crear curso", openedx.studio, "POST", "/api/v1/course_runs/", json={
            "title": product.title,
            "org": product.org,
            "number": product.number,
            "run": product.run,
            "pacing_type": "self_paced" if product.pacing == Product.Pacing.SELF else "instructor_paced",
            "team": team,
        })
    if product.course_key != course_key:
        product.course_key = course_key
        product.save(update_fields=["course_key", "modified"])


def push_dates(product):
    # The course run API needs start *and* end together; course details takes
    # either, but it replaces the whole document, so read it first.
    path = f"/api/contentstore/v1/course_details/{product.course_key}"
    details = _call("leer fechas", openedx.studio, "GET", path)
    details["start_date"] = product.start.isoformat() if product.start else None
    details["end_date"] = product.end.isoformat() if product.end else None
    _call("fechas", openedx.studio, "PUT", path, json=details)


def push_image(product):
    if not product.image:
        return
    suffix = "." + product.image.name.rsplit(".", 1)[-1].lower()
    with product.image.open("rb") as image:
        _call("imagen", openedx.studio, "POST", f"/api/v1/course_runs/{product.course_key}/images/", files={
            "card_image": (f"card{suffix}", image, IMAGE_CONTENT_TYPES.get(suffix, "image/png")),
        })


def sync_sale_mode(product):
    base = f"/api/course_modes/v1/courses/{product.course_key}/"
    modes = _call("leer precio", openedx.lms, "GET", base)
    exists = any(mode["mode_slug"] == "verified" for mode in modes)
    on_sale = product.status == Product.Status.ON_SALE
    if on_sale and exists:
        _call("precio", openedx.lms, "PATCH", f"{base}verified",
              data=json.dumps({"min_price": int(product.price), "currency": "mxn"}),
              headers={"Content-Type": "application/merge-patch+json"})
    elif on_sale:
        _call("precio", openedx.lms, "POST", base, json={
            "course_id": product.course_key, "mode_slug": "verified", "mode_display_name": "Compra",
            "min_price": int(product.price), "currency": "mxn",
        })
    elif exists:
        _call("retirar de la venta", openedx.lms, "DELETE", f"{base}verified")


def create_course(product):
    """
    Draft → created. The course key is saved as soon as the course exists, and
    the status only changes at the end: a draft with a course key is a creation
    that failed half way, and calling this again finishes it.
    """
    ensure_course(product)
    push_dates(product)
    push_image(product)
    if product.status == Product.Status.DRAFT:
        product.status = Product.Status.CREATED
        product.save(update_fields=["status", "modified"])
    log.info("Product %s synced to Open edX as %s", product.pk, product.course_key)
