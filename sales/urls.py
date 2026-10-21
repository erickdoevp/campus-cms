from django.urls import path

from .views import MyOrdersView, checkout, stripe_webhook

urlpatterns = [
    path("checkout/", checkout, name="checkout"),
    path("me/orders/", MyOrdersView.as_view(), name="my-orders"),
    path("webhooks/stripe/", stripe_webhook, name="stripe-webhook"),
]
