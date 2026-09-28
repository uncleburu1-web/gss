from rest_framework import serializers
from .models import Device


class HeartbeatSerializer(serializers.Serializer):
    """What the desktop POS (and, less critically, the Android app) POSTs
    periodically while it has connectivity. `device_id` is generated once,
    client-side, on first launch and reused forever after — it's how the
    same physical install is recognized across every heartbeat.
    """
    device_id = serializers.UUIDField()
    device_type = serializers.ChoiceField(choices=['desktop', 'android'])
    name = serializers.CharField(required=False, allow_blank=True, default='')
    app_version = serializers.CharField(required=False, allow_blank=True, default='')


class DeviceSerializer(serializers.ModelSerializer):
    is_online = serializers.ReadOnlyField()

    class Meta:
        model = Device
        fields = ['id', 'device_type', 'name', 'app_version', 'last_seen_at', 'is_online']
