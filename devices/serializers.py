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
    # Stage 1b: the till's own outbox, so the cloud can tell "on but stuck" (STALE) from fine.
    pending_ops = serializers.IntegerField(required=False, min_value=0)
    failed_ops = serializers.IntegerField(required=False, min_value=0)


class DeviceSerializer(serializers.ModelSerializer):
    is_online = serializers.ReadOnlyField()
    status = serializers.ReadOnlyField()
    last_contact_at = serializers.ReadOnlyField()
    current_user = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ['id', 'device_type', 'name', 'app_version', 'last_seen_at', 'is_online',
                  'status', 'last_contact_at', 'last_sync_at', 'pending_ops', 'failed_ops', 'current_user']

    def get_current_user(self, obj):
        user = obj.current_user
        if user is None:
            return None
        worker = getattr(user, 'worker', None)
        return (worker.full_name if worker is not None and worker.full_name else user.get_username())
