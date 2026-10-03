from rest_framework.routers import DefaultRouter
<<<<<<< HEAD
from .views import LiabilityViewSet

router = DefaultRouter()
router.register(r'liabilities', LiabilityViewSet, basename='liability')
=======
from .views import LiabilityViewSet, LiabilityPaymentViewSet

router = DefaultRouter()
router.register(r'liabilities', LiabilityViewSet, basename='liability')
router.register(r'liability-payments', LiabilityPaymentViewSet, basename='liability-payment')
>>>>>>> 6f155c9 (Add expense support)

urlpatterns = router.urls
