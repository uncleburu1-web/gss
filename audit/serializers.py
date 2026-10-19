from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    shop_name = serializers.CharField(source='shop.name', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ['id', 'at', 'shop', 'shop_name', 'actor_name', 'action', 'target_type', 'target_id',
                  'summary', 'reason', 'data', 'source']
        read_only_fields = fields
