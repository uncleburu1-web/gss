from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from core.permissions import IsOwner
from core.utils import get_shop_for_user, resolve_branch_for_device_login
from .models import Device
from .serializers import HeartbeatSerializer, DeviceSerializer


class HeartbeatView(APIView):
    """The desktop POS calls this every 30-60s while it has connectivity —
    it's the only signal the cloud has that "the desktop is online right
    now". No heartbeat for a while (see devices.models.ONLINE_THRESHOLD)
    and the CEO app's shop-status check reports the desktop as offline,
    even though the cloud API itself is perfectly reachable.

    Deliberately does NOT recompute `shop` from whoever's currently
    logged in on every call — a device is paired to a branch exactly
    once, at login (see core.auth_serializers), and stays that way for
    the device's whole life; recomputing it here would silently let it
    drift to a different branch (or, for a CEO with several branches,
    crash outright — get_shop_for_user has no single branch to return
    without an X-Branch-ID header the desktop never sends). Once paired,
    a heartbeat just refreshes last_seen_at on the SAME shop the device
    already belongs to.
    """

    def post(self, request):
        serializer = HeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        existing = Device.objects.filter(id=data['device_id']).first()
        if existing is not None:
            shop = existing.shop
        elif data['device_type'] == 'desktop':
            # Heartbeat arrived before any login ever paired this device —
            # shouldn't normally happen (login always pairs first) but
            # resolve the same way login would rather than 500.
            shop = resolve_branch_for_device_login(request.user)
        else:
            shop = get_shop_for_user(request.user)  # Android CEO app: no pairing concept, just its own shop
        if shop is None:
            return Response({'detail': 'Could not determine which branch this device belongs to.'}, status=409)

        device, _ = Device.objects.update_or_create(
            id=data['device_id'],
            defaults={
                'shop': shop,
                'device_type': data['device_type'],
                'name': data.get('name', ''),
                'app_version': data.get('app_version', ''),
                'last_seen_at': timezone.now(),
                'is_deleted': False,
            },
        )
        return Response(DeviceSerializer(device).data)


class DeviceListView(APIView):
    """Which devices (desktop tills, CEO phones) have ever connected for
    this shop, and whether each is currently considered online."""

    def get(self, request):
        shop = get_shop_for_user(request.user)
        devices = Device.objects.filter(shop=shop, is_deleted=False)
        return Response(DeviceSerializer(devices, many=True).data)


class DeviceDetailView(APIView):
    """DELETE only — "unpair" a device (Settings → Devices → Remove).
    Soft-deletes the Device row, which is the ONLY thing that pins a
    physical desktop to a branch (see core.auth_serializers): the very
    next login attempt from that same machine, by anyone, is then treated
    as a brand-new device and pairs fresh to whoever logs in. Meant for
    "this PC was repurposed / handed to a different branch" — not a
    routine action.
    """
    permission_classes = [IsOwner]

    def delete(self, request, pk):
        shop = get_shop_for_user(request.user)
        device = Device.objects.filter(id=pk, shop=shop, is_deleted=False).first()
        if device is None:
            return Response({'detail': 'Device not found.'}, status=404)
        device.is_deleted = True
        device.save(update_fields=['is_deleted', 'updated_at'])
        return Response(status=204)
