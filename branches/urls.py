from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BranchModulesView, BranchViewSet

router = DefaultRouter()
router.register(r'branches', BranchViewSet, basename='branch')

urlpatterns = [
    path('branches/<str:pk>/modules/', BranchModulesView.as_view(), name='branch-modules'),
] + router.urls
