import io
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import Product
from core.tests.utils import auth_header, use_test_jwt_keys

MEDIA_ROOT = tempfile.mkdtemp()


def image_file(fmt="PNG", name="portada.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "teal").save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


@use_test_jwt_keys
@override_settings(MEDIA_ROOT=MEDIA_ROOT, COURSE_ORG="Campus")
@mock.patch("core.openedx.user_exists", return_value=True)
class ProductApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        call_command("contador", "contadora")
        self.auth = auth_header(username="contadora")

    def create(self, **data):
        payload = {"title": "Contabilidad básica", "number": "CONT101", "run": "2026_T1", **data}
        return self.client.post(reverse("product-list"), payload, content_type="application/json", **self.auth)

    def test_only_contadores_can_use_it(self, _user_exists):
        response = self.client.get(reverse("product-list"), **auth_header(user_id=99, username="alumno"))
        assert response.status_code == 403

    def test_create_draft(self, user_exists):
        response = self.create(price="1499.00", authors=["profe", "profe", " "], pacing="instructor")

        assert response.status_code == 201, response.content
        data = response.json()
        assert data["status"] == "draft"
        assert data["org"] == "Campus"
        assert data["course_key"] is None
        assert data["currency"] == "mxn"
        assert data["price"] == "1499.00"
        assert data["authors"] == ["profe"]
        user_exists.assert_called_once_with("profe")
        assert Product.objects.get().created_by.username == "contadora"

    def test_unknown_author_is_rejected(self, user_exists):
        user_exists.side_effect = lambda username: username != "nadie"
        response = self.create(authors=["profe", "nadie"])
        assert response.status_code == 400
        assert "nadie" in response.json()["authors"][0]

    def test_invalid_data_is_rejected(self, _user_exists):
        cases = {
            "negative price": {"price": "-1"},
            "bad course code": {"number": "CONT 101"},
            "end before start": {"start": "2026-11-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
        }
        for reason, data in cases.items():
            with self.subTest(reason):
                assert self.create(**data).status_code == 400

    def test_duplicate_course_key_is_rejected(self, _user_exists):
        self.create()
        response = self.create()
        assert response.status_code == 400
        assert "run" in response.json()

    def test_status_and_course_key_are_read_only(self, _user_exists):
        response = self.create(status="on_sale", course_key="course-v1:X+Y+Z")
        assert response.json()["status"] == "draft"
        assert response.json()["course_key"] is None

    def test_course_code_is_locked_once_in_open_edx(self, _user_exists):
        product_id = self.create().json()["id"]
        Product.objects.filter(pk=product_id).update(course_key="course-v1:Campus+CONT101+2026_T1")

        url = reverse("product-detail", args=[product_id])
        response = self.client.patch(url, {"run": "2026_T2"}, content_type="application/json", **self.auth)
        assert response.status_code == 400
        response = self.client.patch(url, {"title": "Otro título"}, content_type="application/json", **self.auth)
        assert response.status_code == 200

    def test_upload_and_replace_image(self, _user_exists):
        product_id = self.create().json()["id"]
        url = reverse("product-image", args=[product_id])

        response = self.client.post(url, {"image": image_file()}, **self.auth)
        assert response.status_code == 200, response.content
        first = Product.objects.get().image
        assert response.json()["image"].startswith("http://testserver/media/products/")

        self.client.post(url, {"image": image_file("JPEG", "otra.jpg")}, **self.auth)
        second = Product.objects.get().image
        assert second.name.endswith(".jpg")
        assert not first.storage.exists(first.name)

    def test_image_must_be_jpg_or_png(self, _user_exists):
        product_id = self.create().json()["id"]
        url = reverse("product-image", args=[product_id])
        response = self.client.post(url, {"image": image_file("GIF", "anim.gif")}, **self.auth)
        assert response.status_code == 400
        response = self.client.post(url, {"image": SimpleUploadedFile("x.png", b"not an image")}, **self.auth)
        assert response.status_code == 400

    def test_delete_only_before_open_edx(self, _user_exists):
        first = self.create().json()["id"]
        second = self.create(run="2026_T2").json()["id"]
        Product.objects.filter(pk=second).update(course_key="course-v1:Campus+CONT101+2026_T2")

        assert self.client.delete(reverse("product-detail", args=[first]), **self.auth).status_code == 204
        assert self.client.delete(reverse("product-detail", args=[second]), **self.auth).status_code == 400

    def test_filter_by_status(self, _user_exists):
        self.create()
        url = reverse("product-list")
        assert len(self.client.get(url, {"status": "draft"}, **self.auth).json()) == 1
        assert len(self.client.get(url, {"status": "on_sale"}, **self.auth).json()) == 0
