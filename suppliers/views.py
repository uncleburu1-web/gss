from rest_framework import viewsets, filters
from core.mixins import ShopScopedMixin
from .models import Supplier
from .serializers import SupplierSerializer


class SupplierViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.filter(is_deleted=False)
    serializer_class = SupplierSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'contact_phone', 'contact_email']
