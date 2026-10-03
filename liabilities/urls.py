from rest_framework.routers import DefaultRouter
from .views import LiabilityViewSet, LiabilityPaymentViewSet

router = DefaultRouter()
router.register(r'liabilities', LiabilityViewSet, basename='liability')
router.register(r'liability-payments', LiabilityPaymentViewSet, basename='liability-payment')

urlpatterns = router.urls
