"""Stage 1b: the cloud, not a UI, decides whether a till is ONLINE, STALE or OFFLINE."""
import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Organization, Shop
from realtime.models import RealtimeEvent
from staff.models import Worker
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op

from .models import Device


def owner_and_shop(name='own'):
    user = User.objects.create_user(username=name, password='x')
    org = Organization.objects.create(name=name, owner=None, business_type='general')
    shop = Shop.objects.create(name=name, organization=org)
    Worker.objects.create(user=user, full_name=name, role='owner', shop=shop)
    return user, shop


def device(shop, **fields):
    return Device.objects.create(id=uuid.uuid4(), shop=shop, device_type='desktop', name='Till', **fields)


class DeviceStatusTests(TestCase):
    def setUp(self):
        self.user, self.shop = owner_and_shop()
        self.now = timezone.now()

    def test_never_heard_from_is_offline(self):
        self.assertEqual(device(self.shop).status, 'offline')

    def test_recent_contact_with_nothing_waiting_is_online(self):
        self.assertEqual(device(self.shop, last_seen_at=self.now).status, 'online')

    def test_a_live_socket_alone_counts_as_contact(self):
        d = device(self.shop, last_seen_at=self.now - timedelta(hours=3), last_ws_seen_at=self.now)
        self.assertEqual(d.status, 'online')
        self.assertTrue(d.is_online)

    def test_on_but_uploads_stuck_is_stale(self):
        d = device(self.shop, last_seen_at=self.now, pending_ops=12, last_sync_at=self.now - timedelta(minutes=20))
        self.assertEqual(d.status, 'stale')

    def test_on_with_failed_uploads_is_stale(self):
        self.assertEqual(device(self.shop, last_seen_at=self.now, failed_ops=1).status, 'stale')

    def test_pending_but_recently_synced_is_online(self):
        d = device(self.shop, last_seen_at=self.now, pending_ops=3, last_sync_at=self.now - timedelta(minutes=1))
        self.assertEqual(d.status, 'online')

    def test_silence_for_5_minutes_is_stale_and_for_20_is_offline(self):
        self.assertEqual(device(self.shop, last_seen_at=self.now - timedelta(minutes=5)).status, 'stale')
        self.assertEqual(device(self.shop, last_seen_at=self.now - timedelta(minutes=20)).status, 'offline')

    def test_heartbeat_records_queue_counts_and_who_is_signed_in(self):
        d = device(self.shop)
        r = client_for(self.user).post('/api/devices/heartbeat/', {
            'device_id': str(d.id), 'device_type': 'desktop', 'pending_ops': 4, 'failed_ops': 1,
        }, format='json')
        self.assertEqual(r.status_code, 200)
        d.refresh_from_db()
        self.assertEqual((d.pending_ops, d.failed_ops, d.current_user), (4, 1, self.user))
        self.assertEqual(r.json()['status'], 'stale')

    def test_device_list_and_ceo_status_report_it(self):
        device(self.shop, last_ws_seen_at=self.now)
        listed = client_for(self.user).get('/api/devices/').json()
        self.assertEqual(listed[0]['status'], 'online')
        status = client_for(self.user).get('/api/ceo/shop-status/').json()
        self.assertTrue(status['desktop_connected'])
        self.assertEqual(status['desktop_status'], 'online')

    def test_an_accepted_push_stamps_last_sync(self):
        d = device(self.shop)
        response, _ = push_from(client_for(self.user), d, make_op('customer', 'create', {'name': 'A'}))
        self.assertEqual(response.status_code, 200)
        d.refresh_from_db()
        self.assertIsNotNone(d.last_sync_at)


class BatchedEventsTests(TestCase):
    """G9: one realtime event per kind of change per push, not one per operation."""

    def test_fifty_customers_in_one_push_make_one_event(self):
        user, shop = owner_and_shop('batch')
        ops = [make_op('customer', 'create', {'name': f'C{i}'}) for i in range(50)]
        with self.captureOnCommitCallbacks(execute=True):
            client_for(user).post('/api/sync/push/', {'operations': ops}, format='json')
        events = RealtimeEvent.objects.filter(shop=shop)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.event_type, 'customer.created')
        self.assertEqual(event.payload['count'], 50)
        self.assertEqual(len(event.payload['ids']), 50)
