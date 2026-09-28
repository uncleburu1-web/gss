from rest_framework import serializers
from .models import Liability


class LiabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Liability
        fields = ['id', 'name', 'category', 'amount', 'due_date', 'status', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']
