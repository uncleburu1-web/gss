from decimal import Decimal
from rest_framework import serializers
from .models import RepairTicket, RepairPart


class AddPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))


class AddPartSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class RepairPartSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    total_cost = serializers.SerializerMethodField()

    class Meta:
        model = RepairPart
        fields = ['id', 'item', 'item_name', 'quantity', 'unit_cost', 'total_cost', 'added_at']
        read_only_fields = ['id', 'unit_cost', 'added_at']

    def get_total_cost(self, obj):
        return obj.quantity * obj.unit_cost


class RepairTicketSerializer(serializers.ModelSerializer):
    balance_due = serializers.ReadOnlyField()
    is_paid = serializers.ReadOnlyField()
    parts_cost = serializers.ReadOnlyField()
    warranty_expires = serializers.ReadOnlyField()
    technician_name = serializers.CharField(source='technician.full_name', read_only=True, default=None)
    parts_used = RepairPartSerializer(many=True, read_only=True)

    class Meta:
        model = RepairTicket
        fields = [
            'id', 'ticket_no', 'customer_name', 'customer_phone', 'device',
            'issue', 'status', 'priority', 'technician', 'technician_name', 'estimated_ready', 'warranty_days',
            'warranty_expires', 'cost', 'payment_status', 'amount_paid', 'balance_due', 'is_paid',
            'parts_cost', 'parts_used', 'notes', 'date_in', 'date_out',
        ]
        read_only_fields = ['id', 'ticket_no', 'date_in', 'date_out']

    def validate(self, attrs):
        cost = attrs.get('cost', getattr(self.instance, 'cost', 0)) or 0
        amount_paid = attrs.get('amount_paid', None)
        if amount_paid is not None and cost and Decimal(str(amount_paid)) > Decimal(str(cost)):
            raise serializers.ValidationError({'amount_paid': 'Amount paid cannot exceed the quoted cost.'})
        return attrs

    def create(self, validated_data):
        cost = validated_data.get('cost', 0) or 0
        payment_status = validated_data.get('payment_status', 'installment')
        initial_paid = validated_data.pop('amount_paid', 0) or 0

        if payment_status == 'paid':
            initial_paid = cost

        ticket = RepairTicket.objects.create(**validated_data)
        if initial_paid > 0:
            ticket.apply_payment(initial_paid)  # logs the ledger entry and finalizes payment_status
        return ticket

    def update(self, instance, validated_data):
        old_amount_paid = instance.amount_paid
        new_cost = validated_data.get('cost', instance.cost)
        requested_status = validated_data.get('payment_status', instance.payment_status)
        requested_amount_paid = validated_data.pop('amount_paid', None)

        validated_data.pop('payment_status', None)
        validated_data.pop('cost', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.cost = new_cost

        if requested_status == 'paid':
            target_paid = new_cost
        elif requested_amount_paid is not None:
            target_paid = requested_amount_paid
        else:
            target_paid = old_amount_paid

        delta = Decimal(str(target_paid)) - Decimal(str(old_amount_paid))

        if delta > 0:
            instance.save()
            instance.apply_payment(delta)  # bumps amount_paid, logs ledger, flips status if fully paid
        else:
            instance.payment_status = requested_status
            instance.save()

        return instance
