import io
import shutil
import tempfile
from unittest import mock

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import Product
from core.tests.utils import auth_header, use_test_jwt_keys

from .fake_openedx import FakeOpenEdx

MEDIA_ROOT = tempfile.mkdtemp()
KEY = "course-v1:Campus+CONT101+2026_T1"


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "teal").save(buffer, "PNG")
    return SimpleUploadedFile("foto.JPG", buffer.getvalue(), content_type="image/png")


@use_test_jwt_keys
@override_settings(MEDIA_ROOT=MEDIA_ROOT, COURSE_ORG="Campus")
@mock.patch("core.openedx.user_exists", return_value=True)
class CourseLifecycleTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        call_command("contador", "contadora")
        self.auth = auth_header(username="contadora")
        self.fake = FakeOpenEdx().install(self)

    def product(self, **data):
        payload = {"title": "Contabilidad básica", "number": "CONT101", "run": "2026_T1", "price": "1499",
                   "start": "2026-11-01T09:00:00-06:00", "authors": ["profe"], "pacing": "instructor", **data}
        response = self.client.post(reverse("product-list"), payload, content_type="application/json", **self.auth)
        assert response.status_code == 201, response.content
        return response.json()["id"]

    def post(self, product_id, name, **kwargs):
        return self.client.post(reverse(f"product-{name}", args=[product_id]), **kwargs, **self.auth)

    def patch(self, product_id, data):
        return self.client.patch(reverse("product-detail", args=[product_id]), data,
                                 content_type="application/json", **self.auth)

    def test_create_course_in_open_edx(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "image", data={"image": png()})

        response = self.post(product_id, "create-course")

        assert response.status_code == 200, response.content
        assert response.json()["status"] == "created"
        assert response.json()["course_key"] == KEY
        course = self.fake.courses[KEY]
        assert course["title"] == "Contabilidad básica"
        assert course["pacing_type"] == "instructor_paced"
        assert course["team"] == [{"user": "profe", "role": "instructor"}]
        assert course["details"]["start_date"] == "2026-11-01T15:00:00+00:00"
        assert course["details"]["end_date"] is None
        assert course["details"]["overview"] == ""  # the rest of the document is sent back untouched
        # A PNG uploaded as .JPG is named and typed after its real format.
        assert course["image"][:2] == ("card.png", "image/png")
        # The mode buyers get enrolled in, ready before the first sale, and one
        # Open edX won't expire before the course end on the next publish.
        mode = self.fake.modes[KEY]["verified"]
        assert (mode["currency"], mode["expiration_datetime"], mode["expiration_datetime_is_explicit"]) == ("mxn", None, True)
        self.fake.publish(KEY, course_end=timezone.now() + timedelta(days=1))
        assert mode["expiration_datetime"] is None

    def test_create_course_needs_start_and_authors(self, _user_exists):
        product_id = self.product(start=None, authors=[])
        response = self.post(product_id, "create-course")
        assert response.status_code == 400
        assert "fecha de inicio" in response.json()[0] and "autor" in response.json()[0]
        assert self.fake.calls == []

    def test_half_done_creation_is_finished_by_retrying(self, _user_exists):
        product_id = self.product()
        self.fake.fail.add("details-put")

        response = self.post(product_id, "create-course")
        assert response.status_code == 502
        product = Product.objects.get()
        assert (product.status, product.course_key) == ("draft", KEY)

        self.fake.fail.clear()
        self.fake.calls.clear()
        response = self.post(product_id, "create-course")
        assert response.status_code == 200, response.content
        assert response.json()["status"] == "created"
        assert "create" not in self.fake.calls  # the existing course was adopted
        assert self.fake.courses[KEY]["team"] == [{"user": "profe", "role": "instructor"}]

    def test_created_course_cannot_be_created_again(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "create-course")
        assert self.post(product_id, "create-course").status_code == 400

    def test_sale_lifecycle_stays_in_the_cms(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "create-course")

        response = self.post(product_id, "put-on-sale")
        assert response.status_code == 400  # no image yet
        self.post(product_id, "image", data={"image": png()})
        self.fake.calls.clear()

        assert self.post(product_id, "put-on-sale").json()["status"] == "on_sale"
        assert self.patch(product_id, {"price": "1299.50"}).json()["price"] == "1299.50"
        assert self.post(product_id, "withdraw").json()["status"] == "created"
        self.post(product_id, "put-on-sale")
        assert self.post(product_id, "archive").json()["status"] == "archived"
        # Only the CMS sells: buyers keep their verified mode and enrollments.
        assert self.fake.calls == []
        assert self.fake.has_verified_mode(KEY)

    def test_access_mode_failure_leaves_a_retryable_creation(self, _user_exists):
        product_id = self.product()
        self.fake.fail.add("modes-post")
        assert self.post(product_id, "create-course").status_code == 502
        assert Product.objects.get().status == "draft"

        self.fake.fail.clear()
        assert self.post(product_id, "create-course").json()["status"] == "created"
        assert self.fake.has_verified_mode(KEY)

    def test_studio_owns_dates_pacing_and_team_once_created(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "create-course")
        for data in ({"start": "2026-12-01T00:00:00Z"}, {"pacing": "self"}, {"authors": ["otra"]}):
            with self.subTest(data):
                assert self.patch(product_id, data).status_code == 400
        # Marketing copy stays in the CMS and can change.
        assert self.patch(product_id, {"title": "Contabilidad desde cero"}).status_code == 200

    def test_new_image_goes_to_open_edx(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "create-course")
        assert self.fake.courses[KEY]["image"] is None
        self.post(product_id, "image", data={"image": png()})
        assert self.fake.courses[KEY]["image"][0] == "card.png"

    def test_price_can_have_centavos(self, _user_exists):
        response = self.client.post(reverse("product-list"), {"title": "X", "number": "X1", "run": "R1", "price": "99.50"},
                                    content_type="application/json", **self.auth)
        assert response.status_code == 201
        assert response.json()["price"] == "99.50"

    def test_price_cannot_be_removed_while_on_sale(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "image", data={"image": png()})
        self.post(product_id, "create-course")
        self.post(product_id, "put-on-sale")
        for price in (None, "0"):
            with self.subTest(price):
                assert self.patch(product_id, {"price": price}).status_code == 400
        assert str(Product.objects.get().price) == "1499.00"
