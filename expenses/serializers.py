from rest_framework import serializers
from .models import Expense, ExpenseCategory


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ['id', 'name', 'is_default', 'created_at']
        read_only_fields = ['id', 'is_default', 'created_at']


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.full_name', read_only=True, default=None)

    class Meta:
        model = Expense
        fields = [
            'id', 'category', 'category_name', 'amount', 'description', 'payment_method',
            'date', 'time', 'recorded_by_name', 'receipt_url', 'notes', 'created_at', 'updated_at',
        ]
        # receipt_url is read-only here for the same reason
        # InventoryItem.image_url is on InventoryItemSerializer — it's
        # only ever set via the dedicated `receipt` upload action (see
        # views.py), never accepted as a plain string, so a client can
        # never point an expense at an arbitrary URL instead of a file
        # this backend actually validated and uploaded itself.
        read_only_fields = ['id', 'created_at', 'updated_at', 'receipt_url']

    def validate_category(self, category):
        # ShopScopedMixin's own queryset filtering keeps the category
        # dropdown itself branch-scoped, but a raw API call could still
        # POST a foreign shop's category id directly — this is the actual
        # enforcement, same reasoning as AttendanceRecordSerializer.validate_worker.
        shop = self.context.get('shop')
        if shop is not None and category.shop_id != shop.id:
            raise serializers.ValidationError('That category is not part of this branch.')
        return category
