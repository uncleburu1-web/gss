from rest_framework.routers import DefaultRouter
from .views import InventoryItemViewSet, ItemVariantViewSet, StockBatchViewSet, StockMovementViewSet, StockTransferViewSet

router = DefaultRouter()
router.register(r'items', InventoryItemViewSet, basename='inventory-item')
router.register(r'variants', ItemVariantViewSet, basename='item-variant')
router.register(r'batches', StockBatchViewSet, basename='stock-batch')
router.register(r'movements', StockMovementViewSet, basename='stock-movement')
router.register(r'transfers', StockTransferViewSet, basename='stock-transfer')

urlpatterns = router.urls
