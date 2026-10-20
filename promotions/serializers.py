from rest_framework import serializers

from .models import Promotion


class PromotionSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True, default=None)
    times_used = serializers.IntegerField(read_only=True, default=0)
    discount_given = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, default=0)

    class Meta:
        model = Promotion
        fields = ['id', 'name', 'kind', 'value', 'applies_to', 'item', 'item_name', 'category', 'min_quantity',
                  'buy_quantity', 'get_quantity', 'starts_at', 'ends_at', 'is_active', 'times_used', 'discount_given',
                  'updated_at']
        read_only_fields = ['id', 'item_name', 'times_used', 'discount_given', 'updated_at']

    def validate(self, attrs):
        get = lambda k: attrs.get(k, getattr(self.instance, k, None))
        kind, scope = get('kind'), get('applies_to')
        if kind == 'percent_off' and not (0 < (get('value') or 0) <= 100):
            raise serializers.ValidationError({'value': 'A percentage between 1 and 100.'})
        if kind == 'amount_off' and not (get('value') or 0) > 0:
            raise serializers.ValidationError({'value': 'How much off each one.'})
        if kind == 'buy_x_get_y' and not ((get('buy_quantity') or 0) > 0 and (get('get_quantity') or 0) > 0):
            raise serializers.ValidationError({'buy_quantity': 'Say how many to buy and how many are free.'})
        if scope == 'item' and not get('item'):
            raise serializers.ValidationError({'item': 'Choose the product.'})
        if scope == 'category' and not get('category'):
            raise serializers.ValidationError({'category': 'Choose the category.'})
        if kind == 'buy_x_get_y' and scope != 'item':
            raise serializers.ValidationError({'applies_to': 'Buy X get Y works on one product.'})
        if get('starts_at') and get('ends_at') and get('ends_at') <= get('starts_at'):
            raise serializers.ValidationError({'ends_at': 'The end must be after the start.'})
        return attrs
