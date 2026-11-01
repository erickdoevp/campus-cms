from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from core.views import health, me

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/v1/me/", me, name="me"),
    path("api/v1/", include("catalog.urls")),
    path("api/v1/", include("sales.urls")),
]

# Uploaded course images, a few hundred KB each: Django serves them in every
# environment (the volume is only mounted in this container). Move them to
# object storage if traffic grows.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
