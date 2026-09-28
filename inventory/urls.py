from rest_framework.routers import DefaultRouter
from .views import InventoryItemViewSet, ItemVariantViewSet, StockBatchViewSet

router = DefaultRouter()
router.register(r'items', InventoryItemViewSet, basename='inventory-item')
router.register(r'variants', ItemVariantViewSet, basename='item-variant')
router.register(r'batches', StockBatchViewSet, basename='stock-batch')

urlpatterns = router.urls
