from rest_framework.routers import DefaultRouter

from .views import ShiftViewSet

router = DefaultRouter()
router.register(r'shifts', ShiftViewSet, basename='shift')
urlpatterns = router.urls
