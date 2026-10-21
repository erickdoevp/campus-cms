"""
Business logic: Stripe checkouts, and turning Stripe events into course access.

The money (Order.status) is decided in a DB transaction from what Stripe says.
The access (Order.access) is granted afterwards through the Open edX API, so a
slow or down Open edX never loses a payment: the order stays paid with
access "error", and reconcile_orders retries it.

Every function that reacts to Stripe is idempotent: Stripe retries webhooks and
can deliver the same event more than once.
"""
import logging
from urllib.parse import quote

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog import openedx_sync
from catalog.models import Product
from core import openedx

from .models import Order

log = logging.getLogger(__name__)


class PurchaseError(Exception):
    """A checkout can't be started; ``code`` is safe to show to API clients."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def user_owns(user, product):
    return Order.objects.filter(user=user, product=product, status=Order.Status.PAID).exists()


def course_page_url(course_key):
    return f"{settings.PUBLIC_SITE_URL}/cursos/{quote(course_key, safe='')}"


def _open_checkout(user, product):
    """
    Return ``(order, url)`` for the user's latest Stripe session for this product
    if it can still be paid, so two tabs/clicks don't create two payable checkouts.
    A price change makes a new checkout.
    """
    order = Order.objects.filter(
        user=user, product=product, status=Order.Status.PENDING, amount=product.price,
        stripe_checkout_session_id__isnull=False,
    ).first()  # newest first (Meta.ordering)
    if order is None:
        return None
    session = stripe.checkout.Session.retrieve(order.stripe_checkout_session_id, api_key=settings.STRIPE_SECRET_KEY)
    if session.status != "open":
        return None
    return order, session.url


def create_checkout(user, course_key):
    """Create a pending Order and its Stripe Checkout Session. Returns ``(order, checkout_url)``."""
    product = Product.objects.filter(course_key=course_key).first()
    if product is None:
        raise PurchaseError("course_not_found", "Curso no encontrado.", status=404)
    if product.status != Product.Status.ON_SALE or not product.price:
        raise PurchaseError("not_purchasable", "Este curso no está a la venta.")
    if user_owns(user, product):
        raise PurchaseError("already_owned", "Ya tienes este curso.", status=409)

    reusable = _open_checkout(user, product)
    if reusable is not None:
        return reusable

    order = Order.objects.create(user=user, product=product, course_key=course_key, amount=product.price)
    page = course_page_url(course_key)
    metadata = {"order_id": str(order.pk), "course_id": course_key}
    session = stripe.checkout.Session.create(
        api_key=settings.STRIPE_SECRET_KEY,
        mode="payment",
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": order.currency,
                # Stripe expects the smallest currency unit (centavos).
                "unit_amount": int(order.amount * 100),
                "product_data": {"name": product.title},
            },
        }],
        customer_email=user.email or None,
        client_reference_id=str(order.pk),
        metadata=metadata,
        payment_intent_data={"metadata": metadata},
        success_url=f"{page}?payment=success",
        cancel_url=f"{page}?payment=cancelled",
        # Payment methods (card, OXXO...) are chosen in the Stripe Dashboard.
        idempotency_key=f"campus-cms-order-{order.pk}",
    )
    order.stripe_checkout_session_id = session.id
    order.save(update_fields=["stripe_checkout_session_id", "modified"])
    return order, session.url


# --- access in Open edX ----------------------------------------------------------


def _set_enrollment(order, is_active):
    """Enroll (or unenroll) the buyer as verified, as the CMS service account."""
    # The enrollment API rejects both if the mode is missing or expired.
    openedx_sync.ensure_access_mode(order.course_key)
    openedx.lms("POST", "/api/enrollment/v1/enrollment", json={
        "user": order.user.username,
        "mode": "verified",
        "is_active": is_active,
        "course_details": {"course_id": order.course_key},
    })


def sync_access(order):
    """
    Make the Open edX enrollment match the order: access while paid, none after
    a refund. Never raises: failures are recorded on the order for reconcile_orders.
    """
    want = order.status == Order.Status.PAID
    target = Order.Access.GRANTED if want else Order.Access.REVOKED
    if order.access == target or (not want and order.access == Order.Access.NONE):
        return order
    try:
        _set_enrollment(order, is_active=want)
    except (openedx.OpenEdxError, openedx_sync.SyncError, OSError) as exc:
        log.warning("Order %s: could not %s access in Open edX: %s", order.pk, "grant" if want else "revoke", exc)
        order.access, order.access_error = Order.Access.ERROR, str(exc)[:2000]
    else:
        order.access, order.access_error = target, ""
        log.info("Order %s: access %s for %s in %s", order.pk, target, order.user.username, order.course_key)
    order.save(update_fields=["access", "access_error", "modified"])
    return order


# --- Stripe events -------------------------------------------------------------


def _order_from_session(session):
    order_id = (session.get("metadata") or {}).get("order_id") or session.get("client_reference_id")
    if not order_id:
        return None
    return Order.objects.select_for_update().select_related("user").filter(pk=order_id).first()


@transaction.atomic
def _record_payment(session):
    order = _order_from_session(session)
    if order is None:
        log.warning("Stripe session %s has no matching order", session.get("id"))
        return None
    if session.get("payment_status") != "paid":
        # e.g. OXXO voucher created but not paid yet: wait for async_payment_succeeded.
        log.info("Order %s: session %s not paid yet (%s)", order.pk, session.get("id"), session.get("payment_status"))
        return order
    if order.status != Order.Status.PENDING:
        return order  # already handled (or refunded)

    payment_intent = session.get("payment_intent") or ""
    already_paid = Order.objects.filter(
        user=order.user, product=order.product, status=Order.Status.PAID,
    ).exclude(pk=order.pk).exists()
    if already_paid:
        # Paid twice for the same course (two checkouts in two tabs, or an OXXO
        # voucher paid after a card). Give the money back automatically.
        stripe.Refund.create(
            api_key=settings.STRIPE_SECRET_KEY,
            payment_intent=payment_intent,
            idempotency_key=f"campus-cms-duplicate-{order.pk}",
        )
        # The money did come in, and went straight back.
        order.status = Order.Status.REFUNDED
        order.paid_at = order.refunded_at = timezone.now()
        order.stripe_payment_intent_id = payment_intent
        order.save(update_fields=["status", "paid_at", "refunded_at", "stripe_payment_intent_id", "modified"])
        log.warning("Order %s: duplicate payment for %s by %s refunded", order.pk, order.course_key, order.user.username)
        return order

    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.stripe_payment_intent_id = payment_intent
    order.save(update_fields=["status", "paid_at", "stripe_payment_intent_id", "modified"])
    log.info("Order %s paid: %s bought %s", order.pk, order.user.username, order.course_key)
    return order


def fulfill_checkout_session(session):
    """
    ``checkout.session.completed`` (cards) and ``checkout.session.async_payment_succeeded``
    (OXXO, paid days later): mark the order paid, then give access.
    """
    order = _record_payment(session)
    if order is not None and order.status == Order.Status.PAID:
        sync_access(order)
    return order


@transaction.atomic
def fail_checkout_session(session):
    """The checkout expired or the async payment (OXXO) failed."""
    order = _order_from_session(session)
    if order is None or order.status != Order.Status.PENDING:
        return order
    order.status = Order.Status.FAILED
    order.save(update_fields=["status", "modified"])
    return order


def refund_charge(charge):
    """A full refund removes the access. Partial refunds keep it."""
    if not charge.get("refunded"):
        return None
    with transaction.atomic():
        order = Order.objects.select_for_update().select_related("user").filter(
            stripe_payment_intent_id=charge.get("payment_intent"), status=Order.Status.PAID,
        ).first()
        if order is None:
            return None
        order.status = Order.Status.REFUNDED
        order.refunded_at = timezone.now()
        order.save(update_fields=["status", "refunded_at", "modified"])
    log.info("Order %s refunded", order.pk)
    return sync_access(order)
