from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import viewsets

import audit
from core.mixins import ShopScopedMixin
from core.permissions import IsOwnerOrReadOnly

from .models import Promotion
from .serializers import PromotionSerializer


class PromotionViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """Owners create and edit promotions; everyone can read them (the checkout needs them).
    ?active=1 returns only those running now."""
    queryset = Promotion.objects.filter(is_deleted=False)
    serializer_class = PromotionSerializer
    permission_classes = [IsOwnerOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset().select_related('item').annotate(
            times_used=Count('sale_items', filter=Q(sale_items__is_deleted=False, sale_items__sale__is_deleted=False)),
            discount_given=Coalesce(Sum('sale_items__discount', filter=Q(sale_items__is_deleted=False, sale_items__sale__is_deleted=False)),
                                    Value(0), output_field=DecimalField(max_digits=14, decimal_places=2)),
        )
        if self.request.query_params.get('active') in ('1', 'true'):
            now = timezone.now()
            qs = qs.filter(is_active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)) \
                   .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        return qs

    def _audit(self, action, promo, extra=''):
        audit.record(action, shop=promo.shop, user=self.request.user, target=promo,
                     summary=f'{promo.name}: {promo.get_kind_display()} {promo.value}{extra}')

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._audit('promotion.created', serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._audit('promotion.changed', serializer.instance, '' if serializer.instance.is_active else ' (stopped)')

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        self._audit('promotion.deleted', instance)
