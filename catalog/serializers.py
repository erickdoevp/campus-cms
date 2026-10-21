from rest_framework import serializers

from core import openedx

from .models import Product

LOCKED_ONCE_CREATED = ("number", "run", "start", "end", "pacing", "authors")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Studio only accepts these for the course card image.
IMAGE_FORMATS = {"JPEG", "PNG"}


class ProductSerializer(serializers.ModelSerializer):
    authors = serializers.ListField(child=serializers.CharField(max_length=150, allow_blank=True), required=False)
    image = serializers.ImageField(read_only=True)
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "title", "org", "number", "run", "course_key", "status",
            "price", "currency", "short_description", "description", "image",
            "start", "end", "pacing", "authors", "created", "modified",
        )
        read_only_fields = ("org", "course_key", "status", "created", "modified")
        # The unique (org, number, run) check is done in validate() with a clearer message.
        validators = []

    def get_currency(self, obj):
        return "mxn"

    def validate_price(self, price):
        # The price is mirrored into Open edX's verified course mode, which
        # only stores whole pesos, while the LMS plugin still does the charging.
        if price is not None and price != price.to_integral_value():
            raise serializers.ValidationError("Por ahora el precio va en pesos enteros, sin centavos.")
        return price

    def validate_authors(self, authors):
        authors = list(dict.fromkeys(a.strip() for a in authors if a.strip()))
        try:
            missing = [a for a in authors if not openedx.user_exists(a)]
        except openedx.OpenEdxError as exc:
            raise serializers.ValidationError("No se pudo consultar Open edX; intenta de nuevo.") from exc
        if missing:
            raise serializers.ValidationError(f"No existen en Open edX: {', '.join(missing)}")
        return authors

    def validate(self, attrs):
        instance = self.instance
        if instance is not None and instance.course_key:
            # The course exists in Open edX: its key can't change, and its
            # dates, pacing and team are managed in Studio from now on.
            for field in LOCKED_ONCE_CREATED:
                if field in attrs and attrs[field] != getattr(instance, field):
                    raise serializers.ValidationError({field: "El curso ya existe en Open edX: esto se cambia en Studio."})
        if instance is not None and instance.status == Product.Status.ON_SALE and "price" in attrs and not attrs["price"]:
            raise serializers.ValidationError({"price": "Está a la venta: retíralo de la venta antes de quitarle el precio."})
        number = attrs.get("number", getattr(instance, "number", None))
        run = attrs.get("run", getattr(instance, "run", None))
        org = instance.org if instance is not None else Product._meta.get_field("org").get_default()
        duplicates = Product.objects.filter(org=org, number=number, run=run)
        if instance is not None:
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError({"run": "Ya hay un curso con este código y periodo."})
        start = attrs.get("start", getattr(instance, "start", None))
        end = attrs.get("end", getattr(instance, "end", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end": "Debe ser posterior al inicio."})
        return attrs


class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.ImageField()

    class Meta:
        model = Product
        fields = ("image",)

    def validate_image(self, image):
        if image.size > MAX_IMAGE_BYTES:
            raise serializers.ValidationError("La imagen pesa más de 5 MB.")
        # Set by the ImageField validation, which already opened it with Pillow.
        if getattr(image, "image", None) is None or image.image.format not in IMAGE_FORMATS:
            raise serializers.ValidationError("Usa una imagen JPG o PNG.")
        # Name it after its real format: Studio picks the content type from it.
        image.name = "image.png" if image.image.format == "PNG" else "image.jpg"
        return image

    def update(self, instance, validated_data):
        old = instance.image.name
        instance = super().update(instance, validated_data)
        if old and old != instance.image.name:
            instance.image.storage.delete(old)
        return instance


class CatalogSerializer(serializers.ModelSerializer):
    """What the public site shows of a product: the commercial side only."""

    id = serializers.CharField(source="course_key")
    image = serializers.ImageField(read_only=True)
    price = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    on_sale = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ("id", "title", "org", "number", "short_description", "description", "image",
                  "price", "currency", "on_sale")

    def get_price(self, obj):
        return int(obj.price) if obj.price is not None else None

    def get_currency(self, obj):
        return "mxn"

    def get_on_sale(self, obj):
        return obj.status == Product.Status.ON_SALE
