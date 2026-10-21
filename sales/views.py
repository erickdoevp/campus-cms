import json
import logging

import stripe
from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from . import services
from .models import Order, PaymentEvent

log = logging.getLogger(__name__)


@api_view(["POST"])
def checkout(request):
    """
    POST /api/v1/checkout/  {"course_id": "course-v1:..."}

    Starts a purchase as the logged-in user and returns the Stripe Checkout URL.
    Access is granted later, by the webhook, once Stripe confirms the payment.
    Errors: {"code": "course_not_found" | "not_purchasable" | "already_owned", "detail"}.
    """
    course_id = str(request.data.get("course_id", ""))
    try:
        order, checkout_url = services.create_checkout(request.user, course_id)
    except services.PurchaseError as exc:
        return Response({"code": exc.code, "detail": str(exc)}, status=exc.status)
    except stripe.StripeError:
        log.exception("Stripe rejected a checkout for %s", course_id)
        return Response({"code": "payment_unavailable", "detail": "No se pudo iniciar el pago. Intenta de nuevo."},
                        status=502)
    return Response({"order_id": order.pk, "checkout_url": checkout_url}, status=201)


class OrderSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source="product.title")

    class Meta:
        model = Order
        fields = ("id", "course_key", "course_title", "amount", "currency", "status", "access", "created", "paid_at")


class MyOrdersView(ListAPIView):
    """GET /api/v1/me/orders/ — the logged-in user's orders, newest first."""

    serializer_class = OrderSerializer

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).select_related("product")


HANDLERS = {
    "checkout.session.completed": services.fulfill_checkout_session,
    "checkout.session.async_payment_succeeded": services.fulfill_checkout_session,
    "checkout.session.async_payment_failed": services.fail_checkout_session,
    "checkout.session.expired": services.fail_checkout_session,
    "charge.refunded": services.refund_charge,
}


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """POST /api/v1/webhooks/stripe/ — Stripe events, verified with the signing secret."""
    payload = request.body.decode("utf-8")
    try:
        stripe.WebhookSignature.verify_header(
            payload, request.headers.get("Stripe-Signature", ""), settings.STRIPE_WEBHOOK_SECRET, tolerance=300,
        )
    except stripe.SignatureVerificationError:
        log.warning("Rejected Stripe webhook with an invalid signature")
        return HttpResponse(status=400)

    event = json.loads(payload)
    try:
        record, _ = PaymentEvent.objects.get_or_create(
            stripe_event_id=event["id"], defaults={"event_type": event["type"], "payload": event},
        )
    except IntegrityError:
        # The same event arriving twice at the same time: the other delivery handles it.
        return HttpResponse(status=200)
    if record.processed_at is not None:
        return HttpResponse(status=200)  # duplicate delivery

    handler = HANDLERS.get(event["type"])
    if handler is not None:
        # An exception here returns 500, so Stripe retries the event later.
        record.order = handler(event["data"]["object"])
    record.processed_at = timezone.now()
    record.save(update_fields=["order", "processed_at"])
    return HttpResponse(status=200)
