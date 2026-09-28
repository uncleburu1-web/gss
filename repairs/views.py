from decimal import Decimal
from django.db import transaction
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.mixins import ShopScopedMixin
from inventory.models import InventoryItem
from .models import RepairTicket, RepairPart
from .serializers import RepairTicketSerializer, AddPaymentSerializer, AddPartSerializer, RepairPartSerializer


def _allocate_part_stock(item, quantity, shop):
    """FEFO stock deduction for one part taken onto a service ticket —
    same batch-walking logic as sales/views.py._allocate_stock, kept as
    its own copy here rather than a shared import because a repair part
    has no SaleAllocation-shaped row to attach to; it just needs the
    weighted cost of what it actually took. select_for_update() matches
    the sales path's concurrency protection (see that function's
    docstring for why it's there and its SQLite caveat)."""
    remaining_to_take = quantity
    batches = item.batches.select_for_update().filter(
        quantity_remaining__gt=0, is_deleted=False
    ).order_by('expiry_date', 'received_date')
    total_cost = Decimal('0')
    total_taken = 0
    for batch in batches:
        if remaining_to_take <= 0:
            break
        take = min(batch.quantity_remaining, remaining_to_take)
        batch.quantity_remaining -= take
        batch.save(update_fields=['quantity_remaining'])
        total_cost += take * batch.cost_price
        total_taken += take
        remaining_to_take -= take

    if total_taken < quantity:
        raise ValueError(f'Only {total_taken} of {item.name} in stock (needed {quantity}).')

    unit_cost = round(total_cost / total_taken, 2) if total_taken else Decimal('0')
    return unit_cost


def _restore_part_stock(part):
    """Undo _allocate_part_stock — puts the quantity back on the item's
    newest-expiry open batch (or oldest-received one if nothing has an
    expiry), which is close enough for a reversal: unlike a sale, a
    repair part was never tied to one specific SaleAllocation row, so
    there's no exact batch to restore it to."""
    batch = part.item.batches.select_for_update().filter(is_deleted=False).order_by('-expiry_date', 'received_date').first()
    if batch is not None:
        batch.quantity_remaining += part.quantity
        batch.save(update_fields=['quantity_remaining'])


class RepairTicketViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = RepairTicket.objects.filter(is_deleted=False).select_related('technician').prefetch_related('parts_used__item')
    serializer_class = RepairTicketSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'payment_status', 'priority', 'technician']
    search_fields = ['ticket_no', 'customer_name', 'device']
    ordering_fields = ['date_in', 'status', 'priority']

    @action(detail=True, methods=['post'], url_path='add-payment')
    def add_payment(self, request, pk=None):
        ticket = self.get_object()
        serializer = AddPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket.apply_payment(serializer.validated_data['amount'])
        return Response(RepairTicketSerializer(ticket).data)

    @action(detail=True, methods=['post'], url_path='add-part')
    @transaction.atomic
    def add_part(self, request, pk=None):
        """Attach a part from inventory to this ticket, deducting real
        stock (FEFO) the same way a sale line does — this is what makes
        the service bench actually POS-grade instead of just a job
        tracker: parts cost shows up on the ticket and in inventory/
        profit reporting instead of being typed into the notes field."""
        ticket = self.get_object()
        serializer = AddPartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            item = InventoryItem.objects.select_for_update().get(
                id=data['item'], shop=ticket.shop, is_deleted=False,
            )
        except InventoryItem.DoesNotExist:
            return Response({'item': 'Not found in this branch\'s inventory.'}, status=400)

        try:
            unit_cost = _allocate_part_stock(item, data['quantity'], ticket.shop)
        except ValueError as exc:
            return Response({'quantity': str(exc)}, status=400)

        part = RepairPart.objects.create(
            ticket=ticket, item=item, quantity=data['quantity'], unit_cost=unit_cost, shop=ticket.shop,
        )
        return Response(RepairPartSerializer(part).data, status=201)

    @action(detail=True, methods=['post'], url_path='remove-part')
    @transaction.atomic
    def remove_part(self, request, pk=None):
        ticket = self.get_object()
        part_id = request.data.get('part')
        try:
            part = ticket.parts_used.select_for_update().get(id=part_id, is_deleted=False)
        except RepairPart.DoesNotExist:
            return Response({'part': 'Not found on this ticket.'}, status=400)

        _restore_part_stock(part)
        part.is_deleted = True
        part.save(update_fields=['is_deleted', 'updated_at'])
        return Response(RepairTicketSerializer(ticket).data)
