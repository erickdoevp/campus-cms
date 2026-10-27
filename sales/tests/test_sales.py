import hashlib
import hmac
import io
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from catalog.tests.fake_openedx import FakeOpenEdx
from core.tests.utils import auth_header, use_test_jwt_keys
from sales.models import Order, PaymentEvent

WEBHOOK_SECRET = "whsec_test"
KEY = "course-v1:Campus+CONT101+2026_T1"


def signed_post(client, event):
    payload = json.dumps(event)
    timestamp = int(time.time())
    signature = hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return client.post(reverse("stripe-webhook"), data=payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}")


def session_event(event_id, event_type, order, payment_status="paid", payment_intent="pi_123"):
    return {"id": event_id, "type": event_type, "data": {"object": {
        "id": order.stripe_checkout_session_id, "object": "checkout.session",
        "client_reference_id": str(order.pk), "metadata": {"order_id": str(order.pk)},
        "payment_status": payment_status, "payment_intent": payment_intent,
    }}}


def refund_event(event_id, payment_intent="pi_123", refunded=True):
    return {"id": event_id, "type": "charge.refunded", "data": {"object": {
        "id": "ch_1", "object": "charge", "payment_intent": payment_intent, "refunded": refunded,
    }}}


@use_test_jwt_keys
@override_settings(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET,
                   PUBLIC_SITE_URL="https://campus.example.com")
class SalesTestCase(TestCase):
    def setUp(self):
        self.fake = FakeOpenEdx().install(self)
        self.fake.modes[KEY] = {"verified": {"mode_slug": "verified"}}
        self.product = Product.objects.create(title="Contabilidad básica", number="CONT101", run="2026_T1",
                                              course_key=KEY, status=Product.Status.ON_SALE, price="1499.50")
        self.auth = auth_header(user_id=11, username="alumna", email="alumna@example.com")
        self.client.get(reverse("me"), **self.auth)  # first login creates the CMS user
        self.user = User.objects.get(username="alumna")

    def make_order(self, **kwargs):
        defaults = dict(user=self.user, product=self.product, course_key=KEY, amount="1499.50",
                        stripe_checkout_session_id="cs_test_1")
        return Order.objects.create(**{**defaults, **kwargs})

    def checkout(self, course_id=KEY):
        return self.client.post(reverse("checkout"), {"course_id": course_id}, content_type="application/json",
                                **self.auth)

    def enrollment(self):
        return self.fake.enrollment("alumna", KEY)


class CheckoutTests(SalesTestCase):
    @mock.patch("stripe.checkout.Session.create",
                return_value=SimpleNamespace(id="cs_test_1", url="https://checkout.stripe.test/1"))
    def test_checkout_creates_pending_order(self, create):
        response = self.checkout()

        assert response.status_code == 201, response.content
        assert response.json()["checkout_url"] == "https://checkout.stripe.test/1"
        order = Order.objects.get()
        assert (order.status, order.access, str(order.amount)) == ("pending", "none", "1499.50")
        kwargs = create.call_args.kwargs
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 149950  # centavos
        assert kwargs["line_items"][0]["price_data"]["currency"] == "mxn"
        assert kwargs["customer_email"] == "alumna@example.com"
        assert kwargs["metadata"] == {"order_id": str(order.pk), "course_id": KEY}
        page = "https://campus.example.com/cursos/course-v1%3ACampus%2BCONT101%2B2026_T1"
        assert kwargs["success_url"] == f"{page}?payment=success"
        assert kwargs["cancel_url"] == f"{page}?payment=cancelled"

    def test_checkout_requires_login(self):
        response = self.client.post(reverse("checkout"), {"course_id": KEY}, content_type="application/json")
        assert response.status_code == 401

    @mock.patch("stripe.checkout.Session.create")
    def test_checkout_rejects_what_is_not_for_sale(self, create):
        assert self.checkout("course-v1:Campus+NOPE+1").json()["code"] == "course_not_found"
        for status in (Product.Status.CREATED, Product.Status.ARCHIVED):
            Product.objects.filter(pk=self.product.pk).update(status=status)
            with self.subTest(status):
                assert self.checkout().json()["code"] == "not_purchasable"
        create.assert_not_called()

    @mock.patch("stripe.checkout.Session.create")
    def test_checkout_rejects_owned_course(self, create):
        self.make_order(status=Order.Status.PAID)
        response = self.checkout()
        assert response.status_code == 409
        assert response.json()["code"] == "already_owned"
        create.assert_not_called()

    @mock.patch("stripe.checkout.Session.create")
    @mock.patch("stripe.checkout.Session.retrieve", return_value=SimpleNamespace(status="open", url="https://checkout.stripe.test/1"))
    def test_checkout_reuses_open_session(self, retrieve, create):
        order = self.make_order()
        response = self.checkout()
        assert response.json() == {"order_id": order.pk, "checkout_url": "https://checkout.stripe.test/1"}
        create.assert_not_called()

    @mock.patch("stripe.checkout.Session.create", return_value=SimpleNamespace(id="cs_test_2", url="https://checkout.stripe.test/2"))
    @mock.patch("stripe.checkout.Session.retrieve", return_value=SimpleNamespace(status="open", url="https://checkout.stripe.test/1"))
    def test_price_change_makes_a_new_checkout(self, retrieve, create):
        self.make_order(amount="999")
        assert self.checkout().json()["checkout_url"] == "https://checkout.stripe.test/2"

    @mock.patch("stripe.checkout.Session.create")
    @mock.patch("stripe.checkout.Session.retrieve")
    def test_paid_session_without_webhook_grants_access_instead_of_new_checkout(self, retrieve, create):
        order = self.make_order()
        session = session_event("evt_x", "checkout.session.completed", order)["data"]["object"]
        retrieve.return_value = SimpleNamespace(status="complete", payment_status="paid", to_dict=lambda: session)

        response = self.checkout()

        assert response.status_code == 409
        assert response.json()["code"] == "already_owned"
        create.assert_not_called()
        order.refresh_from_db()
        assert (order.status, order.access) == ("paid", "granted")
        assert self.enrollment()["is_active"]

    def test_my_orders(self):
        self.make_order(status=Order.Status.PAID)
        data = self.client.get(reverse("my-orders"), **self.auth).json()
        assert [(o["course_key"], o["course_title"], o["status"]) for o in data] == [(KEY, "Contabilidad básica", "paid")]


class WebhookTests(SalesTestCase):
    def test_rejects_bad_signature(self):
        response = self.client.post(reverse("stripe-webhook"), data="{}", content_type="application/json",
                                    HTTP_STRIPE_SIGNATURE="t=1,v1=bad")
        assert response.status_code == 400
        assert not PaymentEvent.objects.exists()

    def test_card_payment_grants_access(self):
        order = self.make_order()
        response = signed_post(self.client, session_event("evt_1", "checkout.session.completed", order))

        assert response.status_code == 200
        order.refresh_from_db()
        assert (order.status, order.access) == ("paid", "granted")
        assert order.paid_at is not None
        assert order.stripe_payment_intent_id == "pi_123"
        assert self.enrollment() == {"mode": "verified", "is_active": True}

    def test_duplicate_event_is_processed_once(self):
        order = self.make_order()
        event = session_event("evt_1", "checkout.session.completed", order)
        signed_post(self.client, event)
        self.fake.calls.clear()
        assert signed_post(self.client, event).status_code == 200
        assert self.fake.calls == []
        assert PaymentEvent.objects.count() == 1

    def test_oxxo_waits_for_async_payment(self):
        order = self.make_order()
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", order, payment_status="unpaid"))
        order.refresh_from_db()
        assert order.status == "pending"
        assert self.enrollment() is None

        signed_post(self.client, session_event("evt_2", "checkout.session.async_payment_succeeded", order))
        order.refresh_from_db()
        assert (order.status, order.access) == ("paid", "granted")

    def test_oxxo_paid_after_the_course_was_withdrawn_still_grants_access(self):
        order = self.make_order()
        Product.objects.filter(pk=self.product.pk).update(status=Product.Status.ARCHIVED)
        signed_post(self.client, session_event("evt_1", "checkout.session.async_payment_succeeded", order))
        assert self.enrollment() == {"mode": "verified", "is_active": True}

    def test_missing_access_mode_is_created_before_enrolling(self):
        """Courses created before the CMS sold them may have no verified mode."""
        self.fake.modes.clear()
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", self.make_order()))
        assert self.fake.has_verified_mode(KEY)
        assert self.enrollment()["is_active"] is True

    def test_course_ending_soon_still_grants_access(self):
        """
        Open edX expires a non-explicit verified mode 10 days before the course
        end: with a course ending tomorrow, buyers couldn't be enrolled.
        """
        self.fake.modes[KEY] = {"verified": {"mode_slug": "verified", "expiration_datetime": None,
                                             "expiration_datetime_is_explicit": False}}
        self.fake.publish(KEY, course_end=timezone.now() + timedelta(days=1))

        signed_post(self.client, session_event("evt_1", "checkout.session.completed", self.make_order()))

        assert self.enrollment() == {"mode": "verified", "is_active": True}
        mode = self.fake.modes[KEY]["verified"]
        assert (mode["expiration_datetime"], mode["expiration_datetime_is_explicit"]) == (None, True)
        self.fake.publish(KEY, course_end=timezone.now() + timedelta(days=1))
        assert mode["expiration_datetime"] is None  # the next publish leaves it alone

    def test_mode_without_expiration_is_marked_explicit(self):
        self.fake.modes[KEY] = {"verified": {"mode_slug": "verified", "expiration_datetime": None,
                                             "expiration_datetime_is_explicit": False}}
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", self.make_order()))
        assert "modes-delete" not in self.fake.calls
        assert self.fake.modes[KEY]["verified"]["expiration_datetime_is_explicit"] is True
        assert self.enrollment()["is_active"] is True

    @mock.patch("stripe.Refund.create")
    def test_second_payment_for_same_course_is_refunded(self, refund_create):
        first = self.make_order()
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", first))
        second = self.make_order(stripe_checkout_session_id="cs_test_2")
        signed_post(self.client, session_event("evt_2", "checkout.session.completed", second, payment_intent="pi_456"))

        refund_create.assert_called_once()
        assert refund_create.call_args.kwargs["payment_intent"] == "pi_456"
        second.refresh_from_db()
        first.refresh_from_db()
        assert second.status == "refunded"
        assert (first.status, first.access) == ("paid", "granted")
        assert self.enrollment()["is_active"] is True

    def test_expired_session_fails_order(self):
        order = self.make_order()
        signed_post(self.client, session_event("evt_1", "checkout.session.expired", order, payment_status="unpaid"))
        order.refresh_from_db()
        assert order.status == "failed"

    def test_full_refund_removes_access(self):
        order = self.make_order()
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", order))
        signed_post(self.client, refund_event("evt_2"))

        order.refresh_from_db()
        assert (order.status, order.access) == ("refunded", "revoked")
        assert self.enrollment() == {"mode": "verified", "is_active": False}

    def test_partial_refund_keeps_access(self):
        order = self.make_order()
        signed_post(self.client, session_event("evt_1", "checkout.session.completed", order))
        signed_post(self.client, refund_event("evt_2", refunded=False))
        order.refresh_from_db()
        assert (order.status, order.access) == ("paid", "granted")

    def test_open_edx_down_keeps_the_payment_and_reconcile_fixes_access(self):
        order = self.make_order()
        self.fake.fail.add("enroll")

        # Stripe gets a 200: the money is recorded even if Open edX can't be reached.
        assert signed_post(self.client, session_event("evt_1", "checkout.session.completed", order)).status_code == 200
        order.refresh_from_db()
        assert (order.status, order.access) == ("paid", "error")
        assert "enroll failed" in order.access_error

        self.fake.fail.clear()
        call_command("reconcile_orders", stdout=io.StringIO(), stderr=io.StringIO())
        order.refresh_from_db()
        assert (order.access, order.access_error) == ("granted", "")
        assert self.enrollment()["is_active"] is True


class ReconcileTests(SalesTestCase):
    def run_reconcile(self):
        out, err = io.StringIO(), io.StringIO()
        call_command("reconcile_orders", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def lms_enrollments(self, *enrollments):
        return mock.patch("core.openedx.request", return_value={"next": None, "results": list(enrollments)})

    @mock.patch("stripe.checkout.Session.retrieve")
    def test_settles_old_pending_orders_with_stripe(self, retrieve):
        paid = self.make_order()
        expired = self.make_order(stripe_checkout_session_id="cs_test_2")
        recent = self.make_order(stripe_checkout_session_id="cs_test_3")
        Order.objects.filter(pk__in=[paid.pk, expired.pk]).update(created=timezone.now() - timedelta(hours=30))
        sessions = {
            "cs_test_1": {"status": "complete", "payment_status": "paid", "payment_intent": "pi_1",
                          "metadata": {"order_id": str(paid.pk)}},
            "cs_test_2": {"status": "expired", "payment_status": "unpaid", "metadata": {"order_id": str(expired.pk)}},
        }
        retrieve.side_effect = lambda session_id, **kw: SimpleNamespace(to_dict=lambda: sessions[session_id])

        with self.lms_enrollments():
            out, _ = self.run_reconcile()

        assert "2 resueltas" in out
        statuses = dict(Order.objects.values_list("pk", "status"))
        assert statuses == {paid.pk: "paid", expired.pk: "failed", recent.pk: "pending"}
        assert self.enrollment()["is_active"] is True

    def test_reports_paid_enrollments_without_order(self):
        self.make_order(status=Order.Status.PAID, access=Order.Access.GRANTED)
        with self.lms_enrollments(
            {"user": "alumna", "course_id": KEY, "mode": "verified", "is_active": True},
            {"user": "colado", "course_id": KEY, "mode": "verified", "is_active": True},
            {"user": "profe", "course_id": KEY, "mode": "audit", "is_active": True},
            {"user": "devuelto", "course_id": KEY, "mode": "verified", "is_active": False},
        ):
            out, err = self.run_reconcile()
        assert "Inscripciones sin orden: 1" in out
        assert "colado" in err and "alumna" not in err
