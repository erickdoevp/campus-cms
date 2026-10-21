from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import health, me

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/v1/me/", me, name="me"),
    path("api/v1/", include("catalog.urls")),
    path("api/v1/", include("sales.urls")),
]

if settings.DEBUG:
    # In production the uploads are served by the web server / object storage.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
