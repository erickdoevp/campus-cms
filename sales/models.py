from django.conf import settings
from django.db import models


class Order(models.Model):
    """
    One attempt by a user to buy one course.

    A product can have several failed/pending orders for the same user, but at
    most one ``paid`` order (enforced by a DB constraint).

    ``status`` is the money (what Stripe says); ``access`` is the course
    access in Open edX, granted through its API after the payment. They are
    separate because Open edX can be down when Stripe confirms a payment:
    ``reconcile_orders`` retries until both agree.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PAID = "paid", "Pagada"
        FAILED = "failed", "Fallida"
        REFUNDED = "refunded", "Reembolsada"

    class Access(models.TextChoices):
        NONE = "none", "Sin acceso"
        GRANTED = "granted", "Con acceso"
        REVOKED = "revoked", "Acceso retirado"
        ERROR = "error", "Error en Open edX"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="orders")
    # Copied at purchase time, like the price, so history doesn't depend on the product.
    course_key = models.CharField(max_length=255, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="mxn")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    access = models.CharField(max_length=16, choices=Access.choices, default=Access.NONE, db_index=True)
    access_error = models.TextField(blank=True)
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, db_index=True)
    # When the money came in and when it went back: what the stats count by.
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    refunded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # True only while status is PAID, NULL otherwise. MySQL ignores conditional
    # unique constraints but allows repeated NULLs in a unique index, so this
    # enforces "at most one paid order per user and product".
    paid_lock = models.BooleanField(null=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(fields=["user", "product", "paid_lock"], name="sales_one_paid_order_per_product"),
        ]

    def save(self, *args, **kwargs):
        self.paid_lock = True if self.status == self.Status.PAID else None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kwargs["update_fields"] = {*update_fields, "paid_lock"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order {self.pk} {self.user} {self.course_key} [{self.status}/{self.access}]"


class PaymentEvent(models.Model):
    """
    Raw Stripe webhook event. The unique ``stripe_event_id`` lets us ignore
    duplicate deliveries, and the payload is kept for auditing.
    """

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField()
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    processed_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.event_type} {self.stripe_event_id}"
