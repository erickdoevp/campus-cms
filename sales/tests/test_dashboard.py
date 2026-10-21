from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product
from catalog.tests.fake_openedx import FakeOpenEdx
from core.tests.utils import auth_header, use_test_jwt_keys
from sales.models import Order

MX = ZoneInfo("America/Mexico_City")


def at(day, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MX)


@use_test_jwt_keys
@override_settings(TIME_ZONE="America/Mexico_City")
class DashboardTests(TestCase):
    def setUp(self):
        call_command("contador", "contadora")
        self.auth = auth_header(username="contadora")
        self.a = Product.objects.create(title="Contabilidad", number="A1", run="R", course_key="course-v1:Campus+A1+R",
                                        status=Product.Status.ON_SALE, price=1000)
        self.b = Product.objects.create(title="Finanzas", number="B1", run="R", course_key="course-v1:Campus+B1+R",
                                        status=Product.Status.ARCHIVED, price=500)
        self.c = Product.objects.create(title="Impuestos", number="C1", run="R", course_key="course-v1:Campus+C1+R",
                                        status=Product.Status.ON_SALE, price=800)
        self.users = [User.objects.create(username=f"alumno{i}", email=f"alumno{i}@example.com") for i in range(4)]

    def sale(self, product, user, paid_at, amount=None, refunded_at=None, **fields):
        status = Order.Status.REFUNDED if refunded_at else Order.Status.PAID
        return Order.objects.create(user=user, product=product, course_key=product.course_key,
                                    amount=amount or product.price, status=status, paid_at=paid_at,
                                    refunded_at=refunded_at, **fields)

    def get(self, **params):
        return self.client.get(reverse("stats"), params, **self.auth)

    def test_totals_series_and_courses(self):
        u = self.users
        self.sale(self.a, u[0], at(5))
        self.sale(self.a, u[1], at(5, 23, 30))   # still the 5th in Mexico City (already the 6th in UTC)
        self.sale(self.b, u[0], at(7), refunded_at=at(8))
        self.sale(self.a, u[2], at(20), refunded_at=at(3, 9) .replace(month=11))  # refunded after the period
        self.sale(self.a, u[3], at(1, 0, 30).replace(month=9, day=28))           # previous period
        Order.objects.create(user=u[3], product=self.a, course_key=self.a.course_key, amount=1000)  # pending

        data = self.get(**{"from": "2026-10-01", "to": "2026-10-31"}).json()

        assert data["granularity"] == "day"
        assert data["totals"] == {
            "gross": "3500.00", "refunds": "500.00", "net": "3000.00", "paid_orders": 4, "refunded_orders": 1,
            "buyers": 3, "average_order": "875.00",
        }
        assert data["previous"]["gross"] == "1000.00"  # Sep 1-30, same length before: the Sep 28 sale
        series = {point["date"]: point for point in data["series"]}
        assert len(series) == 31
        assert (series["2026-10-05"]["gross"], series["2026-10-05"]["paid_orders"]) == ("2000.00", 2)
        assert series["2026-10-06"]["gross"] == "0.00"
        assert (series["2026-10-08"]["refunds"], series["2026-10-08"]["net"]) == ("500.00", "-500.00")
        courses = [(c["title"], c["sold"], c["gross"], c["refunds"], c["net"]) for c in data["courses"]]
        assert courses == [
            ("Contabilidad", 3, "3000.00", "0.00", "3000.00"),
            ("Finanzas", 1, "500.00", "500.00", "0.00"),  # ties on net go by title
            ("Impuestos", 0, "0.00", "0.00", "0.00"),     # on sale, no sales: still listed
        ]
        assert data["attention"] == {"access_errors": 0, "pending": 1}

    def test_previous_period_has_the_same_length(self):
        self.sale(self.a, self.users[0], at(28).replace(month=9))
        data = self.get(**{"from": "2026-10-01", "to": "2026-10-07"}).json()
        assert data["previous"]["gross"] == "1000.00"  # Sep 24-30
        assert data["totals"]["average_order"] is None

    def test_long_ranges_go_weekly(self):
        self.sale(self.a, self.users[0], at(14))  # a Wednesday
        data = self.get(**{"from": "2026-07-01", "to": "2026-10-31"}).json()
        assert data["granularity"] == "week"
        weeks = {p["date"]: p["gross"] for p in data["series"]}
        assert weeks["2026-10-12"] == "1000.00"  # the Monday of that week
        assert all(datetime.fromisoformat(d).weekday() == 0 for d in weeks)

    def test_access_errors_need_attention(self):
        self.sale(self.a, self.users[0], at(5), access=Order.Access.ERROR, access_error="LMS down")
        assert self.get().json()["attention"]["access_errors"] == 1

    def test_defaults_and_validation(self):
        data = self.get().json()
        assert len(data["series"]) == 30
        assert self.get(**{"from": "2026-10-10", "to": "2026-10-01"}).status_code == 400
        assert self.get(**{"from": "ayer"}).status_code == 400
        assert self.get(**{"from": "2025-01-01", "to": "2026-10-01"}).status_code == 400

    def test_only_contadores(self):
        response = self.client.get(reverse("stats"), **auth_header(user_id=99, username="alumno"))
        assert response.status_code == 403


@use_test_jwt_keys
class AdminOrdersTests(TestCase):
    def setUp(self):
        call_command("contador", "contadora")
        self.auth = auth_header(username="contadora")
        self.product = Product.objects.create(title="Contabilidad", number="A1", run="R",
                                              course_key="course-v1:Campus+A1+R", status=Product.Status.ON_SALE,
                                              price=1000)
        self.ana = User.objects.create(username="ana", email="ana@example.com")
        self.beto = User.objects.create(username="beto", email="beto@correo.mx")

    def order(self, user, **fields):
        return Order.objects.create(user=user, product=self.product, course_key=self.product.course_key,
                                    amount=1000, **fields)

    def list(self, **params):
        return self.client.get(reverse("order-list"), params, **self.auth).json()

    def test_lists_and_filters(self):
        self.order(self.ana, status=Order.Status.PAID, access=Order.Access.GRANTED)
        self.order(self.beto, status=Order.Status.PAID, access=Order.Access.ERROR, access_error="LMS down")
        self.order(self.beto)

        data = self.list()
        assert data["count"] == 3
        assert data["results"][0]["username"] == "beto"  # newest first
        assert {r["username"] for r in self.list(access="error")["results"]} == {"beto"}
        assert self.list(status="pending")["count"] == 1
        assert self.list(q="correo.mx")["count"] == 2
        assert self.list(q="ANA")["results"][0]["email"] == "ana@example.com"
        assert self.list(course_key="course-v1:Campus+Z+R")["count"] == 0

    def test_retry_access(self):
        fake = FakeOpenEdx().install(self)
        fake.modes[self.product.course_key] = {"verified": {"mode_slug": "verified"}}
        order = self.order(self.ana, status=Order.Status.PAID, access=Order.Access.ERROR, access_error="LMS down")

        response = self.client.post(reverse("order-retry-access", args=[order.pk]), **self.auth)

        assert response.status_code == 200
        assert (response.json()["access"], response.json()["access_error"]) == ("granted", "")
        assert fake.enrollment("ana", self.product.course_key)["is_active"] is True

    def test_only_contadores(self):
        other = auth_header(user_id=99, username="alumno")
        assert self.client.get(reverse("order-list"), **other).status_code == 403
        order = self.order(self.ana)
        assert self.client.post(reverse("order-retry-access", args=[order.pk]), **other).status_code == 403
