from rest_framework import serializers
from .models import Subscription


class SubscriptionStatusSerializer(serializers.ModelSerializer):
    effective_status = serializers.ReadOnlyField()
    cloud_services_enabled = serializers.ReadOnlyField()
    total_price_ngn = serializers.ReadOnlyField()
    active_branch_count = serializers.ReadOnlyField()
    additional_branches = serializers.ReadOnlyField()
    enterprise_recommended = serializers.ReadOnlyField()

    class Meta:
        model = Subscription
        fields = [
            'is_enterprise', 'billing_cycle', 'status', 'effective_status', 'current_period_end',
            'grace_period_days', 'cloud_services_enabled', 'total_price_ngn',
            'active_branch_count', 'additional_branches', 'enterprise_recommended', 'auto_renew',
        ]
