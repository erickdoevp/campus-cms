from django.db import transaction
from rest_framework import parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from core.permissions import IsContador

from . import openedx_sync
from .models import Product
from .serializers import CatalogSerializer, ProductImageSerializer, ProductSerializer


class OpenEdxUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "openedx_unavailable"


def _sync(func, product):
    try:
        func(product)
    except openedx_sync.SyncError as exc:
        raise OpenEdxUnavailable(str(exc)) from exc


class ProductViewSet(viewsets.ModelViewSet):
    """
    /api/v1/products/ — the accountant's courses for sale.

    JSON for everything but the image, which goes to
    POST /api/v1/products/{id}/image/ as multipart (field ``image``).
    Filter with ?status=draft|created|on_sale|archived.

    Lifecycle actions (POST, no body):
    create-course  draft → created     creates the course in Open edX
    put-on-sale    created → on_sale   needs a price and an image
    withdraw       on_sale → created
    archive        created/on_sale → archived
    """

    serializer_class = ProductSerializer
    permission_classes = [IsContador]

    def get_queryset(self):
        queryset = Product.objects.all()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        old_price = serializer.instance.price
        # If Open edX rejects the new price, the CMS keeps the old one too.
        with transaction.atomic():
            product = serializer.save()
            if product.status == Product.Status.ON_SALE and product.price != old_price:
                _sync(openedx_sync.sync_sale_mode, product)

    def perform_destroy(self, instance):
        if instance.course_key:
            raise ValidationError("El curso ya existe en Open edX: archívalo en lugar de borrarlo.")
        if instance.image:
            instance.image.delete(save=False)
        instance.delete()

    @action(detail=True, methods=["post"], parser_classes=[parsers.MultiPartParser])
    def image(self, request, pk=None):
        product = self.get_object()
        serializer = ProductImageSerializer(product, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if product.course_key:
            # Saved in the CMS either way; uploading it again retries Open edX.
            _sync(openedx_sync.push_image, product)
        return self._product_response(product)

    @action(detail=True, methods=["post"], url_path="create-course")
    def create_course(self, request, pk=None):
        product = self.get_object()
        if product.status != Product.Status.DRAFT:
            raise ValidationError("El curso ya se creó en Open edX.")
        missing = [label for label, value in (("fecha de inicio", product.start), ("al menos un autor", product.authors))
                   if not value]
        if missing:
            raise ValidationError(f"Para crear el curso falta: {', '.join(missing)}.")
        _sync(openedx_sync.create_course, product)
        return self._product_response(product)

    @action(detail=True, methods=["post"], url_path="put-on-sale")
    def put_on_sale(self, request, pk=None):
        product = self.get_object()
        if product.status != Product.Status.CREATED:
            raise ValidationError("Solo un curso creado en Open edX (y no a la venta) se puede poner a la venta.")
        missing = [label for label, value in (("precio", product.price), ("imagen", product.image)) if not value]
        if missing:
            raise ValidationError(f"Para ponerlo a la venta falta: {', '.join(missing)}.")
        return self._change_status(product, Product.Status.ON_SALE)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        product = self.get_object()
        if product.status != Product.Status.ON_SALE:
            raise ValidationError("El curso no está a la venta.")
        return self._change_status(product, Product.Status.CREATED)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        product = self.get_object()
        if product.status not in (Product.Status.CREATED, Product.Status.ON_SALE):
            raise ValidationError("Solo se archiva un curso que ya existe en Open edX; un borrador se borra.")
        return self._change_status(product, Product.Status.ARCHIVED)

    def _change_status(self, product, new_status):
        # The status decides whether Open edX sells it: both change or neither.
        with transaction.atomic():
            product.status = new_status
            product.save(update_fields=["status", "modified"])
            _sync(openedx_sync.sync_sale_mode, product)
        return self._product_response(product)

    def _product_response(self, product):
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)


class CatalogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/catalog/ — public: the courses on sale, for the public site.
    /api/v1/catalog/{course_key}/ — one of them; archived courses are still
    found (buyers keep their access and may land on the page), others aren't.

    Only the commercial side: dates, pacing and content come from Open edX.
    """

    serializer_class = CatalogSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    lookup_field = "course_key"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        if self.action == "retrieve":
            statuses = (Product.Status.ON_SALE, Product.Status.ARCHIVED)
        else:
            statuses = (Product.Status.ON_SALE,)
        return Product.objects.filter(status__in=statuses, course_key__isnull=False).order_by("-created")
