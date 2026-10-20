from rest_framework.routers import DefaultRouter

from .views import PromotionViewSet

router = DefaultRouter()
router.register(r'promotions', PromotionViewSet, basename='promotion')

urlpatterns = router.urls
