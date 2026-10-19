"""
Stage 5/6: audit log, subscription gate (no grace period), retention.

    python manage.py test audit
"""
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Organization, Shop
from devices.models import Device
from inventory.models import InventoryItem, StockBatch
from realtime.models import RealtimeEvent
from staff.models import Worker
from subscriptions.models import Subscription, get_or_create_subscription
from sync.models import SyncOperation
from sync.tests import client_for

from .models import AuditLog


def make_org(name='Shop A'):
    ceo = User.objects.create_user(username=f'ceo-{uuid.uuid4().hex[:6]}', password='x')
    org = Organization.objects.create(name=name, owner=ceo, business_type='general')
    shop = Shop.objects.create(name=name, organization=org)
    Worker.objects.create(user=ceo, full_name='Chief', role='owner', shop=shop)
    seller = User.objects.create_user(username=f'sel-{uuid.uuid4().hex[:6]}', password='x')
    Worker.objects.create(user=seller, full_name='Sola', role='seller', shop=shop)
    return org, shop, ceo, seller


def jwt_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return client


class AuditTrailTests(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.item = InventoryItem.objects.create(shop=self.shop, name='Oil', sell_price=100)
        StockBatch.objects.create(item=self.item, shop=self.shop, batch_number='B', quantity_received=5,
                                  quantity_remaining=5, cost_price=50, selling_price=100)

    def test_damage_is_audited_with_who_and_why(self):
        client_for(self.seller).post(f'/api/inventory/items/{self.item.id}/damage/', {'quantity': 1, 'reason': 'Leaking'}, format='json')
        entry = AuditLog.objects.get(action='stock.damage')
        self.assertEqual((entry.user, entry.actor_name, entry.reason, entry.shop), (self.seller, 'Sola', 'Leaking', self.shop))

    def test_permission_changes_and_staff_changes_are_audited(self):
        client_for(self.ceo).put('/api/control-center/', {'changes': [{'role': 'seller', 'capability': 'view_all_sales', 'allowed': True}]}, format='json')
        self.assertTrue(AuditLog.objects.filter(action='permissions.changed', organization=self.org).exists())
        worker = Worker.objects.get(user=self.seller)
        client_for(self.ceo).patch(f'/api/workers/{worker.id}/', {'role': 'branch_manager'}, format='json')
        self.assertTrue(AuditLog.objects.filter(action='staff.changed', target_id=str(worker.id)).exists())

    def test_entries_cannot_be_changed_or_deleted(self):
        entry = AuditLog.objects.create(action='x')
        entry.summary = 'rewrite'
        with self.assertRaises(ValueError):
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()

    def test_only_owners_read_it(self):
        AuditLog.objects.create(action='sale.deleted', shop=self.shop)
        self.assertEqual(client_for(self.seller).get('/api/audit/').status_code, 403)
        response = client_for(self.ceo).get('/api/audit/')
        self.assertEqual(response.status_code, 200)
        rows = response.json().get('results', response.json())
        self.assertEqual([r['action'] for r in rows], ['sale.deleted'])

    def test_other_organizations_entries_are_invisible(self):
        _org, other_shop, _ceo, _s = make_org('Other')
        AuditLog.objects.create(action='sale.deleted', shop=other_shop)
        rows = client_for(self.ceo).get('/api/audit/').json()
        self.assertEqual(rows.get('results', rows), [])


class SubscriptionGateTests(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.sub = get_or_create_subscription(self.org)

    def expire(self, seconds_ago=1, status='trial'):
        Subscription.objects.filter(pk=self.sub.pk).update(
            status=status, current_period_end=timezone.now() - timedelta(seconds=seconds_ago))

    def test_new_subscriptions_have_no_grace_period(self):
        self.assertEqual(self.sub.grace_period_days, 0)

    def test_the_moment_it_expires_cloud_api_answers_402_for_everyone(self):
        self.assertEqual(jwt_client(self.seller).get('/api/sales/').status_code, 200)
        self.expire(seconds_ago=1)
        for user in (self.seller, self.ceo):
            response = jwt_client(user).get('/api/sales/')
            self.assertEqual(response.status_code, 402)
            self.assertEqual(response.json()['code'], 'subscription_expired')
            self.assertTrue(response.json()['trial_ended'])

    def test_sync_is_paused_too_so_the_till_keeps_its_queue(self):
        self.expire()
        response = jwt_client(self.seller).post('/api/sync/push/', {'operations': []}, format='json')
        self.assertEqual(response.status_code, 402)

    def test_sign_in_status_and_paying_stay_open(self):
        self.expire(status='active')
        client = jwt_client(self.ceo)
        self.assertEqual(client.get('/api/me/').status_code, 200)
        status = client.get('/api/subscription/status/')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['effective_status'], 'expired')
        self.assertFalse(jwt_client(self.ceo).get('/api/sales/').json()['trial_ended'])
        login = APIClient().post('/api/auth/login/', {'username': self.ceo.username, 'password': 'x'}, format='json')
        self.assertNotEqual(login.status_code, 402)

    def test_not_signed_in_still_gets_401_not_402(self):
        self.expire()
        self.assertEqual(APIClient().get('/api/sales/').status_code, 401)

    def test_renewing_reopens_immediately(self):
        self.expire()
        Subscription.objects.filter(pk=self.sub.pk).update(status='active', current_period_end=timezone.now() + timedelta(days=30))
        self.assertEqual(jwt_client(self.seller).get('/api/sales/').status_code, 200)


class RetentionTests(TestCase):
    def setUp(self):
        _org, self.shop, self.user, _s = make_org()
        self.old = timezone.now() - timedelta(days=120)

    def op(self, device=None, at=None):
        row = SyncOperation.objects.create(id=uuid.uuid4(), shop=self.shop, entity_type='customer',
                                           entity_id=uuid.uuid4(), operation='create', device=device)
        SyncOperation.objects.filter(pk=row.pk).update(applied_at=at or self.old)
        return row.pk

    def test_old_ops_go_but_not_those_a_still_offline_till_may_resend(self):
        synced_since = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop',
                                             last_sync_at=timezone.now() - timedelta(days=2))
        offline_for_months = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop',
                                                   last_sync_at=self.old - timedelta(days=1))
        gone = self.op(device=synced_since)
        web = self.op(device=None)
        kept = self.op(device=offline_for_months)
        recent = self.op(device=None, at=timezone.now() - timedelta(days=10))

        call_command('prune_sync_history', stdout=StringIO())
        remaining = set(SyncOperation.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, {kept, recent})
        self.assertNotIn(gone, remaining)
        self.assertNotIn(web, remaining)

    def test_old_events_go_but_each_shops_newest_stays(self):
        for seq in (1, 2, 3):
            RealtimeEvent.objects.create(shop=self.shop, sequence=seq, event_type='sale.created')
        RealtimeEvent.objects.update(created_at=self.old)
        call_command('prune_sync_history', stdout=StringIO())
        left = list(RealtimeEvent.objects.values_list('sequence', flat=True))
        self.assertEqual(left, [3])

    def test_audit_log_is_never_pruned(self):
        entry = AuditLog.objects.create(action='sale.deleted', shop=self.shop)
        AuditLog.objects.filter(pk=entry.pk).update(at=timezone.now() - timedelta(days=4000))
        call_command('nightly_maintenance', stdout=StringIO(), stderr=StringIO())
        self.assertTrue(AuditLog.objects.filter(pk=entry.pk).exists())

    def test_dry_run_deletes_nothing(self):
        self.op()
        out = StringIO()
        call_command('prune_sync_history', '--dry-run', stdout=out)
        self.assertIn('Would delete 1 sync operation', out.getvalue())
        self.assertEqual(SyncOperation.objects.count(), 1)
