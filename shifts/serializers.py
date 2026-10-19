from rest_framework import serializers

from .models import CashMovement, Shift


class CashMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashMovement
        fields = ['id', 'kind', 'amount', 'reason', 'occurred_at']


class ShiftSerializer(serializers.ModelSerializer):
    cashier = serializers.SerializerMethodField()
    device_name = serializers.CharField(source='device.name', default=None, read_only=True)
    branch = serializers.CharField(source='shop.name', read_only=True)

    class Meta:
        model = Shift
        fields = ['id', 'branch', 'device_name', 'cashier', 'owner_username', 'status', 'opened_at', 'opening_float',
                  'closed_at', 'expected_cash', 'counted_cash', 'variance', 'z_number', 'notes']

    def get_cashier(self, obj):
        worker = getattr(obj.user, 'worker', None) if obj.user_id else None
        return worker.full_name if worker is not None and worker.full_name else obj.owner_username


class ShiftDetailSerializer(ShiftSerializer):
    cash_movements = CashMovementSerializer(many=True, read_only=True)

    class Meta(ShiftSerializer.Meta):
        fields = ShiftSerializer.Meta.fields + ['report', 'cash_movements']
