import io
import json
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import Product
from core.openedx import OpenEdxError
from core.tests.utils import auth_header, use_test_jwt_keys

MEDIA_ROOT = tempfile.mkdtemp()
KEY = "course-v1:Campus+CONT101+2026_T1"


class FakeOpenEdx:
    """Just enough of Studio and the LMS course modes API, in memory."""

    def __init__(self):
        self.courses = {}  # course_key -> {"title", "pacing_type", "team", "details", "image"}
        self.modes = {}    # course_key -> {"verified": {...}}
        self.calls = []
        self.fail = set()  # step names that answer 500

    def _check(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise OpenEdxError(f"{name} failed", 500)

    def studio(self, method, path, **kwargs):
        if method == "POST" and path == "/api/v1/course_runs/":
            self._check("create")
            data = kwargs["json"]
            key = f"course-v1:{data['org']}+{data['number']}+{data['run']}"
            self.courses[key] = {**data, "details": {"start_date": None, "end_date": None, "overview": ""}, "image": None}
            return {"id": key}
        if path.startswith("/api/v1/course_runs/"):
            key = path.split("/")[4]
            if key not in self.courses:
                raise OpenEdxError("not found", 404)
            if path.endswith("/images/"):
                self._check("image")
                name, file, content_type = kwargs["files"]["card_image"]
                self.courses[key]["image"] = (name, content_type, file.read()[:4])
            elif method == "PATCH":
                self._check("team")
                self.courses[key]["team"] += [m for m in kwargs["json"]["team"] if m not in self.courses[key]["team"]]
            return {"id": key}
        if path.startswith("/api/contentstore/v1/course_details/"):
            key = path.rsplit("/", 1)[1]
            self._check(f"details-{method.lower()}")
            if method == "PUT":
                self.courses[key]["details"] = kwargs["json"]
            return dict(self.courses[key]["details"])
        raise AssertionError(f"unexpected Studio call {method} {path}")

    def lms(self, method, path, **kwargs):
        key = path.split("/")[5]
        modes = self.modes.setdefault(key, {})
        self._check(f"modes-{method.lower()}")
        if method == "GET":
            return list(modes.values())
        if method == "POST":
            modes[kwargs["json"]["mode_slug"]] = dict(kwargs["json"])
        elif method == "PATCH":
            assert kwargs["headers"]["Content-Type"] == "application/merge-patch+json"
            modes["verified"].update(json.loads(kwargs["data"]))
        elif method == "DELETE":
            del modes["verified"]
        return None

    def verified_price(self, key=KEY):
        """The price the LMS plugin would charge, or None if it doesn't sell the course."""
        mode = self.modes.get(key, {}).get("verified")
        return mode and mode["min_price"]


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
        self.fake = FakeOpenEdx()
        for name in ("studio", "lms"):
            patcher = mock.patch(f"core.openedx.{name}", side_effect=getattr(self.fake, name))
            patcher.start()
            self.addCleanup(patcher.stop)

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
        # Not on sale yet: no verified mode, so the LMS plugin doesn't sell it.
        assert self.fake.verified_price() is None

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

    def test_sale_lifecycle_mirrors_the_price(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "create-course")

        response = self.post(product_id, "put-on-sale")
        assert response.status_code == 400  # no image yet
        self.post(product_id, "image", data={"image": png()})

        assert self.post(product_id, "put-on-sale").json()["status"] == "on_sale"
        assert self.fake.verified_price() == 1499
        assert self.fake.modes[KEY]["verified"]["currency"] == "mxn"

        assert self.patch(product_id, {"price": "1299"}).status_code == 200
        assert self.fake.verified_price() == 1299

        assert self.post(product_id, "withdraw").json()["status"] == "created"
        assert self.fake.verified_price() is None

        self.post(product_id, "put-on-sale")
        assert self.fake.verified_price() == 1299
        assert self.post(product_id, "archive").json()["status"] == "archived"
        assert self.fake.verified_price() is None

    def test_open_edx_failure_keeps_cms_unchanged(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "image", data={"image": png()})
        self.post(product_id, "create-course")
        self.fake.fail.add("modes-post")
        assert self.post(product_id, "put-on-sale").status_code == 502
        assert Product.objects.get().status == "created"

        self.fake.fail = {"modes-patch"}
        assert self.post(product_id, "put-on-sale").status_code == 200
        assert self.patch(product_id, {"price": "999"}).status_code == 502
        assert str(Product.objects.get().price) == "1499.00"
        assert self.fake.verified_price() == 1499

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

    def test_price_must_be_whole_pesos(self, _user_exists):
        response = self.client.post(reverse("product-list"), {"title": "X", "number": "X1", "run": "R1", "price": "99.50"},
                                    content_type="application/json", **self.auth)
        assert response.status_code == 400
        assert "price" in response.json()

    def test_price_cannot_be_removed_while_on_sale(self, _user_exists):
        product_id = self.product()
        self.post(product_id, "image", data={"image": png()})
        self.post(product_id, "create-course")
        self.post(product_id, "put-on-sale")
        for price in (None, "0"):
            with self.subTest(price):
                assert self.patch(product_id, {"price": price}).status_code == 400
        assert self.fake.verified_price() == 1499
