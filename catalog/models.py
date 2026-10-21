import uuid
from pathlib import Path

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

# Allowed in an Open edX course key part (kept stricter than Open edX itself).
course_key_part = RegexValidator(r"^[A-Za-z0-9_.-]+$", "Usa solo letras, números, guion, guion bajo o punto.")


def default_org():
    return settings.COURSE_ORG


def product_image_path(instance, filename):
    # Random names: uploads never collide and don't leak the original name.
    return f"products/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Product(models.Model):
    """
    A course for sale. The CMS owns everything commercial about it (price,
    sale status, marketing copy, image); Open edX owns the course content.
    Both are linked by ``course_key`` once the course exists in Open edX.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        CREATED = "created", "Creado en Open edX"
        ON_SALE = "on_sale", "A la venta"
        ARCHIVED = "archived", "Archivado"

    class Pacing(models.TextChoices):
        SELF = "self", "A tu ritmo"
        INSTRUCTOR = "instructor", "Con fechas del instructor"

    title = models.CharField(max_length=255)
    # Parts of the course key: course-v1:{org}+{number}+{run}.
    org = models.CharField(max_length=64, default=default_org, editable=False)
    number = models.CharField(max_length=64, validators=[course_key_part])
    run = models.CharField(max_length=64, validators=[course_key_part])
    course_key = models.CharField(max_length=255, unique=True, null=True, blank=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # Always MXN. Null while the accountant hasn't decided it yet.
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                validators=[MinValueValidator(0)])

    short_description = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=product_image_path, blank=True)

    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    pacing = models.CharField(max_length=16, choices=Pacing.choices, default=Pacing.SELF)
    # Open edX usernames that get the instructor role when the course is created.
    authors = models.JSONField(default=list, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                   related_name="+", editable=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-modified"]
        constraints = [
            models.UniqueConstraint(fields=["org", "number", "run"], name="catalog_product_unique_course_key_parts"),
        ]

    def __str__(self):
        return self.title

    @property
    def planned_course_key(self):
        return f"course-v1:{self.org}+{self.number}+{self.run}"
