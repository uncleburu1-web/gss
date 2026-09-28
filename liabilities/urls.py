from rest_framework.routers import DefaultRouter
from .views import LiabilityViewSet

router = DefaultRouter()
router.register(r'liabilities', LiabilityViewSet, basename='liability')

urlpatterns = router.urls
