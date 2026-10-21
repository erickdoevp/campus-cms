from rest_framework import serializers

from core import openedx

from .models import Product

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
            # The course already exists in Open edX under this key.
            for field in ("number", "run"):
                if field in attrs and attrs[field] != getattr(instance, field):
                    raise serializers.ValidationError({field: "No se puede cambiar: el curso ya existe en Open edX."})
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
        return image

    def update(self, instance, validated_data):
        old = instance.image.name
        instance = super().update(instance, validated_data)
        if old and old != instance.image.name:
            instance.image.storage.delete(old)
        return instance
