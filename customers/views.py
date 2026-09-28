from django.db.models import Sum
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from core.mixins import ShopScopedMixin
from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.filter(is_deleted=False)
    serializer_class = CustomerSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'phone', 'email']

    @action(detail=True, methods=['get'])
    def balance(self, request, pk=None):
        """GET /api/customers/{id}/balance/ — what this customer currently
        owes, summed across every one of their not-yet-fully-paid sales.
        The shop sells on credit (installment sales, see Sale.status
        'outstanding'), so "how much does this person owe us" is the one
        thing a plain contacts list can't answer on its own."""
        customer = self.get_object()
        sales = customer.sales.filter(is_deleted=False, status='outstanding')
        total_owed = sum((s.balance_due for s in sales), start=0)
        return Response({
            'customer_id': str(customer.id),
            'outstanding_sale_count': sales.count(),
            'total_owed': str(total_owed),
        })
