from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework import viewsets

from core.capabilities import has_capability
from core.mixins import ShopScopedMixin

from .models import Shift
from .serializers import ShiftDetailSerializer, ShiftSerializer


def sees_everyone(user):
    """Owner, branch manager, CEO, or a role granted "See every seller's sales" in the Control Center."""
    return has_capability(user, 'view_all_sales')


class ShiftViewSet(ShopScopedMixin, viewsets.ReadOnlyModelViewSet):
    """GET /api/shifts/ and /api/shifts/<id>/ -- the tills' shifts and Z-reports, read-only.

    Shifts are created only by the tills (sync). Who sees what: everyone at the branch for an
    owner / manager / CEO / role with view_all_sales; otherwise only your own shifts.
    Filters: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&status=open|closed"""
    queryset = Shift.objects.filter(is_deleted=False).select_related('device', 'user', 'shop')

    def get_serializer_class(self):
        return ShiftDetailSerializer if self.action == 'retrieve' else ShiftSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not sees_everyone(user):
            qs = qs.filter(Q(user=user) | Q(owner_username=user.get_username()))
        params = self.request.query_params
        if params.get('status') in ('open', 'closed'):
            qs = qs.filter(status=params['status'])
        date_from = parse_date(params.get('date_from') or '')
        date_to = parse_date(params.get('date_to') or '')
        if date_from:
            qs = qs.filter(opened_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(opened_at__date__lte=date_to)
        return qs.prefetch_related('cash_movements') if self.action == 'retrieve' else qs
