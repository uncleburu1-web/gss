from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from core.permissions import is_owner
from core.utils import get_shops_for_user

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/audit/?action=&date_from=&date_to= — owners, branch managers and the CEO only.

    Scoped like every other read: a CEO sees every branch (or the one X-Branch-ID names), anyone
    else their own branch. Entries with no branch (organization-wide, e.g. billing) are shown to
    the organization's CEO."""
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        user = self.request.user
        if not is_owner(user):
            raise PermissionDenied('Only an owner can see the audit log.')
        from django.db.models import Q
        from core.utils import get_organization_for_user
        shops = get_shops_for_user(user, branch_id=self.request.headers.get('X-Branch-ID') or None)
        scope = Q(shop__in=shops)
        org = get_organization_for_user(user)
        if org is not None:
            scope |= Q(shop__isnull=True, organization=org)
        qs = AuditLog.objects.filter(scope).select_related('shop')
        params = self.request.query_params
        if params.get('action'):
            qs = qs.filter(action__startswith=params['action'])
        if params.get('date_from'):
            qs = qs.filter(at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(at__date__lte=params['date_to'])
        return qs.order_by('-at')
