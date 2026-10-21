from rest_framework import parsers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.permissions import IsContador

from .models import Product
from .serializers import ProductImageSerializer, ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    """
    /api/v1/products/ — the accountant's courses for sale.

    JSON for everything but the image, which goes to
    POST /api/v1/products/{id}/image/ as multipart (field ``image``).
    Filter with ?status=draft|created|on_sale|archived.
    """

    serializer_class = ProductSerializer
    permission_classes = [IsContador]

    def get_queryset(self):
        queryset = Product.objects.all()
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

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
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)
