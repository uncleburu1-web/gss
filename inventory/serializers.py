from rest_framework import serializers
from .models import InventoryItem, ItemVariant, StockBatch


class ItemVariantSerializer(serializers.ModelSerializer):
    label = serializers.ReadOnlyField()
    quantity = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()

    class Meta:
        model = ItemVariant
        fields = ['id', 'item', 'size', 'color', 'sku', 'is_active', 'label', 'quantity', 'is_low_stock']
        read_only_fields = ['id']

    def validate(self, attrs):
        size = attrs.get('size', getattr(self.instance, 'size', ''))
        color = attrs.get('color', getattr(self.instance, 'color', ''))
        if not size and not color:
            raise serializers.ValidationError('Give the variant a size, a color, or both.')
        return attrs


class StockBatchSerializer(serializers.ModelSerializer):
    supplier_display = serializers.SerializerMethodField()
    is_expiring_soon = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    item_name = serializers.CharField(source='item.name', read_only=True)
    variant_label = serializers.CharField(source='variant.label', read_only=True, default=None)

    class Meta:
        model = StockBatch
        fields = [
            'id', 'item', 'item_name', 'variant', 'variant_label', 'batch_number', 'quantity_received',
            'quantity_remaining', 'cost_price', 'selling_price', 'expiry_date', 'supplier', 'supplier_name',
            'supplier_display', 'received_date', 'is_expiring_soon', 'is_expired',
        ]
        read_only_fields = ['id', 'quantity_remaining', 'received_date']

    def to_internal_value(self, data):
        # HTML/JS forms send '' for an untouched optional date or select field —
        # DRF's DateField/PrimaryKeyRelatedField reject '' outright, so normalize
        # blank optional fields to None before validation runs.
        if hasattr(data, 'copy'):
            data = data.copy()
        else:
            data = dict(data)
        for field in ('expiry_date', 'supplier', 'variant'):
            if data.get(field) == '':
                data[field] = None
        return super().to_internal_value(data)

    def validate(self, attrs):
        item = attrs.get('item') or getattr(self.instance, 'item', None)
        variant = attrs.get('variant', getattr(self.instance, 'variant', None))
        if item is not None and item.has_variants and variant is None:
            raise serializers.ValidationError({
                'variant': 'This item has variants — pick which size/color this stock is for.'
            })
        if variant is not None and item is not None and variant.item_id != item.id:
            raise serializers.ValidationError({'variant': 'That variant does not belong to this item.'})
        return attrs

    def get_supplier_display(self, obj):
        if obj.supplier:
            return obj.supplier.name
        return obj.supplier_name or None

    def create(self, validated_data):
        validated_data['quantity_remaining'] = validated_data['quantity_received']
        return super().create(validated_data)

    def update(self, instance, validated_data):
        new_received = validated_data.get('quantity_received', instance.quantity_received)
        if new_received != instance.quantity_received:
            delta = new_received - instance.quantity_received
            new_remaining = instance.quantity_remaining + delta
            already_sold = instance.quantity_received - instance.quantity_remaining
            if new_remaining < 0:
                raise serializers.ValidationError({
                    'quantity_received': (
                        f'Cannot reduce received quantity below what has already been sold '
                        f'({already_sold} sold from this batch so far).'
                    )
                })
            instance.quantity_remaining = new_remaining
        return super().update(instance, validated_data)


class InventoryItemSerializer(serializers.ModelSerializer):
    quantity = serializers.ReadOnlyField()
    cost_price = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()
    stock_value = serializers.ReadOnlyField()
    batch_count = serializers.ReadOnlyField()
    has_variants = serializers.ReadOnlyField()

    class Meta:
        model = InventoryItem
        fields = [
            'id', 'name', 'short_code', 'barcode', 'category', 'brand', 'unit', 'spec',
            'quantity', 'min_stock', 'prescription_required', 'cost_price', 'sell_price', 'is_low_stock',
            'stock_value', 'batch_count', 'has_variants', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def to_internal_value(self, data):
        # Same reasoning as StockBatchSerializer: an untouched barcode field
        # from an HTML form arrives as '' — normalize to None so it hits the
        # `null=True` branch of the unique constraint instead of colliding
        # with every other barcode-less product's ''.
        if hasattr(data, 'copy'):
            data = data.copy()
        else:
            data = dict(data)
        if data.get('barcode') == '':
            data['barcode'] = None
        return super().to_internal_value(data)


class InventoryItemDetailSerializer(InventoryItemSerializer):
    batches = StockBatchSerializer(many=True, read_only=True)
    variants = ItemVariantSerializer(many=True, read_only=True)

    class Meta(InventoryItemSerializer.Meta):
        fields = InventoryItemSerializer.Meta.fields + ['batches', 'variants']

