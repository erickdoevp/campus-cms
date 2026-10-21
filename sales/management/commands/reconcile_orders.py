"""
Makes orders, Stripe and Open edX agree. Safe to run any time; run it daily
(cron, Kubernetes CronJob...) and after an Open edX outage.
"""
from datetime import timedelta
from urllib.parse import urlencode

import stripe
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from catalog.models import Product
from core import openedx
from sales import services
from sales.models import Order


class Command(BaseCommand):
    help = (
        "Retries access for paid/refunded orders that Open edX didn't get, settles pending orders "
        "whose webhook never arrived, and reports paid enrollments with no paid order."
    )

    def add_arguments(self, parser):
        parser.add_argument("--pending-hours", type=int, default=24,
                            help="Check with Stripe pending orders older than this (default 24).")

    def handle(self, pending_hours, **options):
        self.retry_access()
        self.settle_pending(timedelta(hours=pending_hours))
        self.report_unpaid_enrollments()

    def retry_access(self):
        orders = Order.objects.select_related("user").filter(
            Q(status=Order.Status.PAID) & ~Q(access=Order.Access.GRANTED)
            | Q(status=Order.Status.REFUNDED, access__in=[Order.Access.GRANTED, Order.Access.ERROR])
        )
        fixed = failed = 0
        for order in orders:
            services.sync_access(order)
            if order.access == Order.Access.ERROR:
                failed += 1
                self.stderr.write(f"Orden {order.pk}: Open edX sigue fallando: {order.access_error[:200]}")
            else:
                fixed += 1
        self.stdout.write(f"Acceso: {fixed} corregidas, {failed} con error")

    def settle_pending(self, age):
        orders = Order.objects.filter(
            status=Order.Status.PENDING, stripe_checkout_session_id__isnull=False, created__lt=timezone.now() - age,
        )
        settled = 0
        for order in orders:
            session = stripe.checkout.Session.retrieve(order.stripe_checkout_session_id,
                                                       api_key=settings.STRIPE_SECRET_KEY).to_dict()
            if session.get("status") == "complete" and session.get("payment_status") == "paid":
                services.fulfill_checkout_session(session)
            elif session.get("status") == "expired":
                services.fail_checkout_session(session)
            else:
                continue  # still open, or an OXXO voucher waiting to be paid
            settled += 1
        self.stdout.write(f"Pendientes: {settled} resueltas con Stripe")

    def report_unpaid_enrollments(self):
        course_keys = list(Product.objects.exclude(course_key=None).values_list("course_key", flat=True))
        if not course_keys:
            return
        paid = set(Order.objects.filter(status=Order.Status.PAID).values_list("user__username", "course_key"))
        query = urlencode({"course_ids": ",".join(course_keys), "page_size": 100})
        url = f"{settings.OPENEDX_LMS_URL}/api/enrollment/v1/enrollments?{query}"
        unpaid = []
        while url:
            page = openedx.request("GET", url)
            for enrollment in page["results"]:
                key = (enrollment["user"], enrollment["course_id"])
                if enrollment["mode"] == "verified" and enrollment["is_active"] and key not in paid:
                    unpaid.append(key)
            url = page.get("next")
        for username, course_key in unpaid:
            self.stderr.write(f"Inscrito sin orden pagada: {username} en {course_key}")
        self.stdout.write(f"Inscripciones sin orden: {len(unpaid)}")
