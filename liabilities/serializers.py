from rest_framework import serializers
<<<<<<< HEAD
from .models import Liability


class LiabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Liability
        fields = ['id', 'name', 'category', 'amount', 'due_date', 'status', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']
=======
from .models import Liability, LiabilityPayment


class LiabilityPaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.CharField(source='recorded_by.full_name', read_only=True, default=None)
    counts_as_expense = serializers.BooleanField(read_only=True)

    class Meta:
        model = LiabilityPayment
        fields = [
            'id', 'liability', 'amount', 'payment_method', 'paid_at',
            'recorded_by_name', 'notes', 'counts_as_expense', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate_liability(self, liability):
        shop = self.context.get('shop')
        if shop is not None and liability.shop_id != shop.id:
            raise serializers.ValidationError('That liability is not part of this branch.')
        return liability

    def validate_amount(self, amount):
        if amount <= 0:
            raise serializers.ValidationError('Payment amount must be greater than zero.')
        return amount

    def validate(self, attrs):
        # Overpayment guard — using .instance's liability when editing an
        # existing payment (liability itself isn't editable on update; see
        # LiabilityPaymentViewSet), otherwise the liability named in the
        # request.
        liability = attrs.get('liability') or (self.instance.liability if self.instance else None)
        amount = attrs.get('amount', self.instance.amount if self.instance else None)
        if liability and amount is not None:
            already_paid = liability.amount_paid
            if self.instance:
                already_paid -= self.instance.amount  # don't double-count the payment being edited
            if already_paid + amount > liability.amount:
                remaining = liability.amount - already_paid
                raise serializers.ValidationError(
                    f'That would overpay this liability — at most {remaining} is outstanding.'
                )
        return attrs


class LiabilitySerializer(serializers.ModelSerializer):
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default=None)
    payments = LiabilityPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Liability
        fields = [
            'id', 'name', 'category', 'owed_to', 'amount', 'amount_paid', 'outstanding',
            'due_date', 'status', 'notes', 'created_by_name', 'cleared_at', 'payments', 'created_at',
        ]
        # status/cleared_at are maintained by Liability.refresh_status(),
        # never set directly by a client — see LiabilityPaymentViewSet,
        # the only thing that ever calls it. amount is editable only on
        # create: see validate_amount below.
        read_only_fields = ['id', 'status', 'cleared_at', 'created_at']

    def validate_amount(self, amount):
        if self.instance and self.instance.payments.filter(is_deleted=False).exists():
            raise serializers.ValidationError(
                'This liability already has payments recorded — the original amount can\'t be changed. '
                'Record an additional liability instead if the obligation has grown.'
            )
        if amount <= 0:
            raise serializers.ValidationError('Amount must be greater than zero.')
        return amount
>>>>>>> 6f155c9 (Add expense support)
