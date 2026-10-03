from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
<<<<<<< HEAD
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
=======

from core.permissions import HasCapability
from core.mixins import ShopScopedMixin
from realtime.events import broadcast
from .models import Liability, LiabilityPayment
from .serializers import LiabilitySerializer, LiabilityPaymentSerializer


class LiabilityViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Liability.objects.filter(is_deleted=False).prefetch_related('payments')
    permission_classes = [HasCapability('manage_liabilities')]
    serializer_class = LiabilitySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category', 'status']
    search_fields = ['name', 'owed_to']

    def perform_create(self, serializer):
        marker = getattr(self.request.user, 'worker', None)
        instance = serializer.save(shop=self.get_current_shop(), created_by=marker)
        broadcast(instance.shop, 'liability.created', {'id': str(instance.pk)})


class LiabilityPaymentViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """Recording a payment here is the ONLY way a Liability's status ever
    changes — see Liability.refresh_status, called after every write
    below. Nothing sets `status`/`cleared_at` directly (LiabilitySerializer
    makes both read-only)."""
    queryset = LiabilityPayment.objects.filter(is_deleted=False).select_related('liability', 'recorded_by')
    serializer_class = LiabilityPaymentSerializer
    permission_classes = [HasCapability('manage_liabilities')]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['liability', 'payment_method']
    ordering_fields = ['paid_at']

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['shop'] = self.get_current_shop()
        return context

    def perform_create(self, serializer):
        marker = getattr(self.request.user, 'worker', None)
        instance = serializer.save(shop=self.get_current_shop(), recorded_by=marker)
        instance.liability.refresh_status()
        broadcast(instance.shop, 'liabilitypayment.created', {'id': str(instance.pk), 'liability_id': str(instance.liability_id)})
        broadcast(instance.shop, 'liability.updated', {'id': str(instance.liability_id)})

    def perform_update(self, serializer):
        instance = serializer.save()
        instance.liability.refresh_status()
        broadcast(instance.shop, 'liabilitypayment.updated', {'id': str(instance.pk), 'liability_id': str(instance.liability_id)})
        broadcast(instance.shop, 'liability.updated', {'id': str(instance.liability_id)})

    def perform_destroy(self, instance):
        liability = instance.liability
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted', 'updated_at'])
        liability.refresh_status()
        broadcast(instance.shop, 'liabilitypayment.deleted', {'id': str(instance.pk), 'liability_id': str(liability.pk)})
        broadcast(instance.shop, 'liability.updated', {'id': str(liability.pk)})
>>>>>>> 6f155c9 (Add expense support)
