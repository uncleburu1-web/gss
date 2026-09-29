from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.permissions import IsOwnerOrReadOnly
from core.mixins import ShopScopedMixin
from realtime.events import broadcast
from .cloudinary_utils import InvalidProductImage, delete_product_image, upload_product_image, validate_product_image
from .models import InventoryItem, ItemVariant, StockBatch
from .serializers import (
    InventoryItemSerializer, InventoryItemDetailSerializer, ItemVariantSerializer, StockBatchSerializer,
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
        serializer = StockBatchSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(shop=self.get_current_shop())
        item.refresh_from_db()
        return Response(InventoryItemDetailSerializer(item).data, status=201)

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
