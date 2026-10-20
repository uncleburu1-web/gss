from decimal import Decimal
from rest_framework import serializers

from promotions.models import Promotion
from inventory.models import InventoryItem, ItemVariant
from customers.models import Customer
from .models import Sale, SaleItem


class SaleItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.ReadOnlyField()
    tax_amount = serializers.ReadOnlyField()
    total = serializers.ReadOnlyField()
    profit = serializers.ReadOnlyField()
    margin_percent = serializers.ReadOnlyField()
    item = serializers.PrimaryKeyRelatedField(
        queryset=InventoryItem.objects.all(), required=False, allow_null=True
    )
    variant = serializers.PrimaryKeyRelatedField(
        queryset=ItemVariant.objects.all(), required=False, allow_null=True
    )
    promotion = serializers.PrimaryKeyRelatedField(
        queryset=Promotion.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )

    class Meta:
        model = SaleItem
        fields = [
            'id', 'item', 'variant', 'variant_label', 'item_name', 'category', 'quantity', 'unit_price',
            'unit_cost', 'discount', 'promotion', 'tax_rate', 'subtotal', 'tax_amount', 'total', 'profit', 'margin_percent',
            'stock_shortfall',
        ]
        read_only_fields = ['id', 'stock_shortfall']

    def validate(self, attrs):
        item = attrs.get('item')
        variant = attrs.get('variant')
        quantity = attrs.get('quantity', 1)

        if quantity < 1:
            raise serializers.ValidationError({
                'quantity': 'Quantity must be at least 1.'
            })
        if variant is not None and item is not None and variant.item_id != item.id:
            raise serializers.ValidationError({'variant': 'That variant does not belong to this item.'})
        discount = attrs.get('discount') or 0
        if discount < 0 or discount > (attrs.get('unit_price') or 0) * quantity:
            raise serializers.ValidationError({'discount': 'A discount must be between zero and what the line costs.'})
        if item is not None and item.has_variants and variant is None:
            raise serializers.ValidationError({'variant': f'"{item.name}" has sizes/colors — pick which one is being sold.'})
        # Check stock against the specific variant sold, not the item's
        # total across every size/color — otherwise ringing up a Medium
        # would succeed just because a shop still has Larges on the shelf.
        available = variant.quantity if variant is not None else (item.quantity if item is not None else None)
        if available is not None and available < quantity:
            what = f'{item.name} ({variant.label})' if variant is not None else item.name
            raise serializers.ValidationError(
                f'Only {available} of "{what}" left in stock — cannot sell {quantity}.'
            )
        return attrs


class SaleSerializer(serializers.ModelSerializer):
    """The cart header. `items` is the writable line list — POST a cart as
    `{ ...header fields..., items: [ {item, quantity, unit_price, ...}, ... ] }`.
    Stock allocation (FEFO across batches) happens in the view, same as
    before, just looped per item now instead of once.
    """
    items = SaleItemSerializer(many=True)
    subtotal = serializers.ReadOnlyField()
    discount = serializers.ReadOnlyField()
    tax_amount = serializers.ReadOnlyField()
    total = serializers.ReadOnlyField()
    profit = serializers.ReadOnlyField()
    margin_percent = serializers.ReadOnlyField()
    balance_due = serializers.ReadOnlyField()
    worker_name = serializers.CharField(source='worker.full_name', read_only=True, default=None)
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.all(), required=False, allow_null=True)
    # Returns taken on this sale (newest first) and, per line, how many units came back.
    returns = serializers.SerializerMethodField()
    returned_quantities = serializers.SerializerMethodField()

    def get_returns(self, obj):
        return [{
            'id': str(r.id), 'returned_at': r.returned_at, 'reason': r.reason, 'refund_method': r.refund_method,
            'refund_amount': str(r.refund_amount), 'total_value': str(r.total_value), 'actor_name': r.actor_name,
            'lines': [{'sale_item': str(l.sale_item_id), 'quantity': l.quantity, 'restocked': l.restocked}
                      for l in r.lines.all()],
        } for r in obj.returns.filter(is_deleted=False).prefetch_related('lines')]

    def get_returned_quantities(self, obj):
        from django.db.models import Sum
        from .models import SaleReturnLine
        rows = SaleReturnLine.objects.filter(sale_return__sale=obj, sale_return__is_deleted=False) \
            .values('sale_item_id').annotate(q=Sum('quantity'))
        return {str(r['sale_item_id']): r['q'] for r in rows}

    class Meta:
        model = Sale
        fields = [
            'id', 'invoice_number', 'receipt_no', 'items', 'subtotal', 'discount', 'tax_amount', 'total', 'profit', 'margin_percent',
            'status', 'amount_paid', 'balance_due',
            'customer', 'customer_name', 'staff_name', 'worker_name', 'payment_method', 'date',
            'returns', 'returned_quantities',
        ]
        read_only_fields = ['id', 'invoice_number', 'receipt_no', 'date', 'worker_name', 'returns', 'returned_quantities']

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('A sale needs at least one item.')
        return value

    def validate(self, attrs):
        status = attrs.get('status', 'completed')
        amount_paid = attrs.get('amount_paid')
        if status == 'outstanding' and amount_paid in (None, 0):
            raise serializers.ValidationError(
                'Enter the amount already paid for an outstanding (installment) sale.'
            )
        return attrs

    # Actual creation (stock allocation, item rows) is handled in
    # SaleViewSet.perform_create — it needs transaction control and FEFO
    # batch logic that doesn't belong in a serializer. This create() only
    # covers the plain header+items case for anything that calls the
    # serializer directly (e.g. tests).
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        sale = Sale.objects.create(**validated_data)
        for item_data in items_data:
            SaleItem.objects.create(sale=sale, shop=sale.shop, **item_data)
        return sale


class AddPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    # Optional: how this installment was paid. Omitted -> the sale's own payment method, as before.
    method = serializers.ChoiceField(choices=['cash', 'transfer', 'pos', 'mobile', 'other'], required=False)


class ReplaceSaleItemSerializer(serializers.Serializer):
    """Swap what a single cart line is for — e.g. customer bought a ₦5,000
    charger and wants a ₦6,000 one instead. Recomputes stock and reports
    the balance owed either way, at the whole-sale level (since payment is
    collected per sale, not per line)."""
    sale_item = serializers.PrimaryKeyRelatedField(
        queryset=SaleItem.objects.all(), required=False,
        help_text='Which line to replace. Omit only if the sale has exactly one item.',
    )
    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all(), required=False, allow_null=True)
    variant = serializers.PrimaryKeyRelatedField(queryset=ItemVariant.objects.all(), required=False, allow_null=True)
    item_name = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))

    def validate(self, attrs):
        item = attrs.get('item')
        variant = attrs.get('variant')
        quantity = attrs.get('quantity', 1)

        if quantity < 1:
            raise serializers.ValidationError({
                'quantity': 'Quantity must be at least 1.'
            })
        if variant is not None and item is not None and variant.item_id != item.id:
            raise serializers.ValidationError({'variant': 'That variant does not belong to this item.'})
        if item is not None and item.has_variants and variant is None:
            raise serializers.ValidationError({'variant': f'"{item.name}" has sizes/colors — pick which one is being given.'})
        available = variant.quantity if variant is not None else (item.quantity if item is not None else None)
        if available is not None and available < quantity:
            what = f'{item.name} ({variant.label})' if variant is not None else item.name
            raise serializers.ValidationError(
                f'Only {available} of "{what}" left in stock — cannot give {quantity}.'
            )
        return attrs


class ReturnLineInputSerializer(serializers.Serializer):
    sale_item = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    restock = serializers.BooleanField(default=True)


class ReturnInputSerializer(serializers.Serializer):
    lines = ReturnLineInputSerializer(many=True)
    reason = serializers.CharField(max_length=255, allow_blank=True)
    refund_method = serializers.ChoiceField(choices=['cash', 'transfer', 'pos', 'mobile', 'other'], default='cash')
    refund_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
