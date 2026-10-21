from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product


@override_settings(COURSE_ORG="Campus")
class PublicCatalogTests(TestCase):
    def product(self, number, status, **fields):
        key = f"course-v1:Campus+{number}+2026_T1" if status != Product.Status.DRAFT else None
        return Product.objects.create(title=f"Curso {number}", number=number, run="2026_T1", status=status,
                                      course_key=key, price=1499, **fields)

    def detail(self, course_key):
        return self.client.get(reverse("catalog-detail", args=[course_key]))

    def test_lists_only_courses_on_sale(self):
        self.product("A1", Product.Status.DRAFT)
        self.product("B1", Product.Status.CREATED)
        self.product("C1", Product.Status.ON_SALE, short_description="Corto", description="Largo")
        self.product("D1", Product.Status.ARCHIVED)

        response = self.client.get(reverse("catalog-list"))

        assert response.status_code == 200
        assert response.json() == [{
            "id": "course-v1:Campus+C1+2026_T1", "title": "Curso C1", "org": "Campus", "number": "C1",
            "short_description": "Corto", "description": "Largo", "image": None,
            "price": 1499, "currency": "mxn", "on_sale": True,
        }]

    def test_detail_finds_on_sale_and_archived_courses(self):
        self.product("C1", Product.Status.ON_SALE)
        self.product("D1", Product.Status.ARCHIVED)
        self.product("B1", Product.Status.CREATED)

        assert self.detail("course-v1:Campus+C1+2026_T1").json()["on_sale"] is True
        assert self.detail("course-v1:Campus+D1+2026_T1").json()["on_sale"] is False
        assert self.detail("course-v1:Campus+B1+2026_T1").status_code == 404

    def test_detail_accepts_an_encoded_course_key(self):
        self.product("C1", Product.Status.ON_SALE)
        response = self.client.get(f"/api/v1/catalog/{quote('course-v1:Campus+C1+2026_T1', safe='')}/")
        assert response.status_code == 200

    def test_ignores_credentials(self):
        """Public: a stale or bad token from the site must not turn it into a 401."""
        self.product("C1", Product.Status.ON_SALE)
        response = self.client.get(reverse("catalog-list"), HTTP_AUTHORIZATION="JWT not-a-token")
        assert response.status_code == 200
        assert len(response.json()) == 1
