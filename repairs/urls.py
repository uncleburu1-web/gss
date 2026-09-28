from rest_framework.routers import DefaultRouter
from .views import RepairTicketViewSet

router = DefaultRouter()
router.register(r'tickets', RepairTicketViewSet, basename='repair-ticket')

urlpatterns = router.urls
