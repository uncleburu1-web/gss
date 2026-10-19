from django.db import transaction
from django.db.models import Q
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.capabilities import has_capability
from core.permissions import IsOwnerOrReadOnly, is_owner
from core.mixins import ShopScopedMixin
from realtime.events import broadcast
from .cloudinary_utils import InvalidProductImage, delete_product_image, upload_product_image, validate_product_image
import audit
from . import ledger
from .models import InventoryItem, ItemVariant, StockBatch, StockMovement
from .serializers import (
    DamageSerializer, InventoryItemSerializer, InventoryItemDetailSerializer, ItemVariantSerializer,
    StockBatchSerializer, StockCountSerializer, StockMovementSerializer,
)


class InventoryItemViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = InventoryItem.objects.filter(is_deleted=False)
    permission_classes = [IsOwnerOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category']
    search_fields = ['name', 'short_code', 'barcode', 'brand', 'category']
    ordering_fields = ['name', 'updated_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return InventoryItemDetailSerializer
        return InventoryItemSerializer

    @action(detail=False, methods=['get'], url_path='by-barcode')
    def by_barcode(self, request):
        """GET /api/inventory/items/by-barcode/?code=<scanned value> — exact-match
        lookup for a barcode scanner, which types fast and hits Enter; a fuzzy
        `search=` match isn't the right tool for that, this is."""
        code = request.query_params.get('code', '').strip()
        if not code:
            return Response({'detail': 'code query param is required.'}, status=400)
        item = self.get_queryset().filter(barcode=code).first()
        if item is None:
            return Response({'detail': 'No product with that barcode.'}, status=404)
        return Response(InventoryItemSerializer(item).data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        items = [i for i in self.get_queryset() if i.is_low_stock]
        return Response(InventoryItemSerializer(items, many=True).data)

    @action(detail=True, methods=['get', 'post'], url_path='batches')
    def batches(self, request, pk=None):
        item = self.get_object()
        if request.method == 'GET':
            qs = item.batches.filter(is_deleted=False)
            return Response(StockBatchSerializer(qs, many=True).data)

        data = {**request.data, 'item': item.id}
        serializer = StockBatchSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save(shop=self.get_current_shop())
        item.refresh_from_db()
        return Response(InventoryItemDetailSerializer(item).data, status=201)

    def _variant_for(self, item, variant_id):
        if not variant_id:
            if item.has_variants:
                raise ledger.LedgerError('This item has sizes/colours — say which one.')
            return None
        variant = item.variants.filter(id=variant_id, is_deleted=False).first()
        if variant is None:
            raise ledger.LedgerError('That variant does not belong to this item.')
        return variant

    def _after_stock_change(self, item, movements):
        broadcast(item.shop, 'inventoryitem.updated', {'id': str(item.pk)})
        broadcast(item.shop, 'stockmovement.created', {'id': str(movements[0].id) if movements else None,
                                                       'count': len(movements)})

    @action(detail=True, methods=['post'], url_path='damage', permission_classes=[IsAuthenticated])
    def damage(self, request, pk=None):
        """POST {quantity, reason, variant?} — write off damaged/expired/lost stock.

        Any worker may record damage (it is something a seller sees first), but never without a
        reason, and never more than is in stock. Who and when are recorded on the movement."""
        item = self.get_object()
        serializer = DamageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                InventoryItem.objects.select_for_update().filter(pk=item.pk).first()
                movements = ledger.record_damage(
                    item.shop, item, data['quantity'], data['reason'],
                    variant=self._variant_for(item, data.get('variant')), user=request.user, source='web',
                )
                audit.record('stock.damage', shop=item.shop, user=request.user, target=item, reason=data['reason'],
                             summary=f"Wrote off {-sum(m.quantity for m in movements)} x {item.name} as damaged",
                             data={'quantity': str(data['quantity'])})
        except ledger.LedgerError as exc:
            return Response({'detail': str(exc)}, status=400)
        self._after_stock_change(item, movements)
        item.refresh_from_db()
        return Response(InventoryItemDetailSerializer(item).data, status=201)

    @action(detail=True, methods=['post'], url_path='stock-count', permission_classes=[IsAuthenticated])
    def stock_count(self, request, pk=None):
        """POST {counted_quantity, reason, variant?} — set stock to what was physically counted.

        Owners only (owner, branch manager, CEO). Records the system and counted figures."""
        if not is_owner(request.user):
            return Response({'detail': 'Only an owner can adjust stock after a count.'}, status=403)
        item = self.get_object()
        serializer = StockCountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                InventoryItem.objects.select_for_update().filter(pk=item.pk).first()
                movements = ledger.record_stock_count(
                    item.shop, item, data['counted_quantity'], data['reason'],
                    variant=self._variant_for(item, data.get('variant')), user=request.user, source='web',
                )
                if movements:
                    audit.record('stock.count', shop=item.shop, user=request.user, target=item, reason=data['reason'],
                                 summary=f'Stock count of {item.name}: system {movements[0].system_quantity}, '
                                         f'counted {movements[0].counted_quantity}',
                                 data={'system': str(movements[0].system_quantity),
                                       'counted': str(movements[0].counted_quantity)})
        except ledger.LedgerError as exc:
            return Response({'detail': str(exc)}, status=400)
        if movements:
            self._after_stock_change(item, movements)
        item.refresh_from_db()
        return Response(InventoryItemDetailSerializer(item).data, status=201 if movements else 200)

    @action(
        detail=True, methods=['post', 'delete'], url_path='image',
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def image(self, request, pk=None):
        """POST (multipart/form-data, file field named `image`) uploads a
        photo for this product -- or replaces the existing one in place --
        via Cloudinary. DELETE removes it. This is deliberately its own
        endpoint rather than a field on the normal create/update body:
        Cloudinary needs actual file bytes, which the plain JSON product
        contract was never designed to carry, and this keeps that contract
        (`{"name": ..., "price": ..., "stock": ...}` with no image, and
        every existing client that sends exactly that) completely
        unchanged. IsOwnerOrReadOnly (this viewset's permission_classes)
        already restricts both methods to the shop owner, same as any
        other product write.
        """
        item = self.get_object()

        if request.method == 'DELETE':
            delete_product_image(item.cloudinary_public_id)
            item.image_url = None
            item.cloudinary_public_id = ''
            item.save(update_fields=['image_url', 'cloudinary_public_id', 'updated_at'])
            broadcast(item.shop, 'inventoryitem.updated', {'id': str(item.pk)})
            return Response(InventoryItemSerializer(item).data)

        file_obj = request.data.get('image')
        try:
            validate_product_image(file_obj)
        except InvalidProductImage as exc:
            return Response({'detail': str(exc)}, status=400)

        try:
            url, public_id = upload_product_image(file_obj, previous_public_id=item.cloudinary_public_id or None)
        except Exception:
            return Response({'detail': 'Could not upload this image right now — please try again.'}, status=502)

        item.image_url = url
        item.cloudinary_public_id = public_id
        item.save(update_fields=['image_url', 'cloudinary_public_id', 'updated_at'])
        broadcast(item.shop, 'inventoryitem.updated', {'id': str(item.pk)})
        return Response(InventoryItemSerializer(item).data)

    @action(detail=True, methods=['get', 'post'], url_path='variants')
    def variants(self, request, pk=None):
        """GET lists this item's variants (sizes/colors); POST adds one —
        mirrors the `batches` action above. Adding the *first* variant to
        an item doesn't touch any batches that already exist directly on
        the item (StockBatch.variant left null) — those keep counting
        toward the item's total stock exactly as before; only new batches
        going forward are expected to name a variant (see
        StockBatchSerializer.validate)."""
        item = self.get_object()
        if request.method == 'GET':
            qs = item.variants.filter(is_deleted=False)
            return Response(ItemVariantSerializer(qs, many=True).data)

        data = {**request.data, 'item': item.id}
        serializer = ItemVariantSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(shop=self.get_current_shop())
        item.refresh_from_db()
        return Response(InventoryItemDetailSerializer(item).data, status=201)


class ItemVariantViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """Direct access to individual variants — editing (rename a size,
    retire a color) or deleting one. Matches StockBatchViewSet's role for
    batches: creation goes through the nested action above, single-record
    edits come through here."""
    queryset = ItemVariant.objects.filter(is_deleted=False).select_related('item')
    permission_classes = [IsOwnerOrReadOnly]
    serializer_class = ItemVariantSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['item']
    ordering_fields = ['size', 'color']


class StockBatchViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """Direct access to individual batches — editing, write-offs, deletion."""
    queryset = StockBatch.objects.filter(is_deleted=False).select_related('item', 'variant', 'supplier')
    permission_classes = [IsOwnerOrReadOnly]
    serializer_class = StockBatchSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['item', 'variant']
    ordering_fields = ['expiry_date', 'received_date']

    @transaction.atomic
    def perform_destroy(self, instance):
        # Deleting a batch takes whatever was left in it out of stock; the ledger says so.
        if instance.quantity_remaining:
            ledger.record(instance.shop, instance, -instance.quantity_remaining, StockMovement.ADJUSTMENT,
                          user=self.request.user, reason='Batch deleted', reference=('stock_batch', instance.id))
        audit.record('stock.batch_deleted', shop=instance.shop, user=self.request.user, target=instance,
                     summary=f'Deleted batch {instance.batch_number or instance.id} of {instance.item.name} '
                             f'({instance.quantity_remaining} left in it)')
        super().perform_destroy(instance)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/inventory/movements/?item=&kind=&date_from=&date_to= — the stock ledger.

    Owners, managers, the CEO and roles granted view_all_sales see every movement; anyone else
    sees the movements they made themselves."""
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['item', 'kind', 'variant', 'batch']

    def get_queryset(self):
        from core.utils import get_shops_for_user
        shops = get_shops_for_user(self.request.user, branch_id=self.request.headers.get('X-Branch-ID') or None)
        qs = StockMovement.objects.filter(shop__in=shops).select_related('item', 'variant')
        if not has_capability(self.request.user, 'view_all_sales'):
            qs = qs.filter(Q(user=self.request.user))
        params = self.request.query_params
        if params.get('date_from'):
            qs = qs.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(created_at__date__lte=params['date_to'])
        return qs.order_by('-created_at')
