from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import MyOrdersView, OrderViewSet, checkout, stats, stripe_webhook

router = SimpleRouter()
router.register("orders", OrderViewSet, basename="order")

urlpatterns = router.urls + [
    path("stats/", stats, name="stats"),
    path("checkout/", checkout, name="checkout"),
    path("me/orders/", MyOrdersView.as_view(), name="my-orders"),
    path("webhooks/stripe/", stripe_webhook, name="stripe-webhook"),
]
