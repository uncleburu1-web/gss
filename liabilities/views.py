from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from core.permissions import IsOwner
from core.mixins import ShopScopedMixin
from .models import Liability
from .serializers import LiabilitySerializer


class LiabilityViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Liability.objects.filter(is_deleted=False)
    permission_classes = [IsOwner]
    serializer_class = LiabilitySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category', 'status']
    search_fields = ['name']
