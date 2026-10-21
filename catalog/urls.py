from rest_framework.routers import DefaultRouter

from .views import CatalogViewSet, ProductViewSet

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("catalog", CatalogViewSet, basename="catalog")
urlpatterns = router.urls
