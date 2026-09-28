from rest_framework.routers import DefaultRouter
from .views import WorkerViewSet, AttendanceRecordViewSet

router = DefaultRouter()
router.register(r'workers', WorkerViewSet, basename='worker')
router.register(r'attendance', AttendanceRecordViewSet, basename='attendance')

urlpatterns = router.urls
