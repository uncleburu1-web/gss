"""
Stage 1 regression tests (architecture analysis, gaps G3, G4 and G6).

G3  A till's sync is scoped to the branch its DEVICE is paired to, so a CEO
    with several branches can sync from a till (before: 403 on every push).
G4  The pull cursor overlaps the previous read, so a row committed just after
    a pull read the table is still delivered by the next pull.
G6  Reusing an operation id with different content is rejected, never
    silently reported as already applied.

    python manage.py test sync
"""
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import Organization, Shop
from customers.models import Customer
from devices.models import Device
from staff.models import Worker

from .models import SyncOperation
from .tests import client_for, make_op, make_shop_user, push
from .views import PULL_CURSOR_OVERLAP

PULL_URL = '/api/sync/pull/'
RECONCILE_URL = '/api/sync/reconcile/'


def make_ceo_with_two_branches(username='ceo'):
    """Same shape registration produces: the org's owner, plus a Worker(owner)
    row on the first branch."""
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name='Chain', owner=user, business_type='general')
    branch_a = Shop.objects.create(name='Branch A', organization=org)
    branch_b = Shop.objects.create(name='Branch B', organization=org)
    Worker.objects.create(user=user, full_name='The CEO', role='owner', shop=branch_a)
    return user, branch_a, branch_b


def make_device(shop, **extra):
    return Device.objects.create(id=uuid.uuid4(), shop=shop, device_type='desktop', name='Till', **extra)


def push_from(client, device, *operations):
    headers = {'HTTP_X_DEVICE_ID': str(device) if not isinstance(device, Device) else str(device.id)}
    response = client.post('/api/sync/push/', {'operations': list(operations)}, format='json', **headers)
    body = response.json() if response.status_code == 200 else {}
    return response, {r['id']: r for r in body.get('results', [])}


class DeviceScopedSyncTests(TestCase):
    """G3."""

    def setUp(self):
        self.ceo, self.branch_a, self.branch_b = make_ceo_with_two_branches()
        self.till_b = make_device(self.branch_b)

    def test_without_a_device_header_a_multi_branch_ceo_is_still_refused(self):
        # Documents the old failure mode (web/Android behaviour is unchanged).
        response, _ = push(client_for(self.ceo), make_op('customer', 'create', {'name': 'X'}))
        self.assertEqual(response.status_code, 403)

    def test_a_multi_branch_ceo_syncs_into_the_branch_the_till_is_paired_to(self):
        op = make_op('customer', 'create', {'name': 'Ada'})
        response, results = push_from(client_for(self.ceo), self.till_b, op)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(results[op['id']]['status'], 'applied')
        self.assertTrue(Customer.objects.filter(id=op['entity_id'], shop=self.branch_b).exists())
        self.assertFalse(Customer.objects.filter(shop=self.branch_a).exists())
        recorded = SyncOperation.objects.get(id=op['id'])
        self.assertEqual(recorded.shop, self.branch_b)
        self.assertEqual(recorded.device, self.till_b)
        self.assertEqual(recorded.user, self.ceo)

    def test_a_seller_cannot_sync_through_another_branchs_till(self):
        seller, _own_shop = make_shop_user('sam', 'Elsewhere')
        op = make_op('customer', 'create', {'name': 'Intruder'})
        response, _ = push_from(client_for(seller), self.till_b, op)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Customer.objects.filter(id=op['entity_id']).exists())

    def test_a_branch_seller_on_their_own_till_works_with_the_header(self):
        seller, shop = make_shop_user('sue', 'Corner Shop')
        till = make_device(shop)
        op = make_op('customer', 'create', {'name': 'Bo'})
        response, results = push_from(client_for(seller), till, op)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(results[op['id']]['status'], 'applied')
        self.assertTrue(Customer.objects.filter(id=op['entity_id'], shop=shop).exists())

    def test_a_removed_till_is_told_to_sign_in_again(self):
        self.till_b.is_deleted = True
        self.till_b.save(update_fields=['is_deleted'])
        response, _ = push_from(client_for(self.ceo), self.till_b, make_op('customer', 'create', {'name': 'Z'}))
        self.assertEqual(response.status_code, 410)

    def test_a_garbage_device_header_is_a_400_not_a_crash(self):
        response, _ = push_from(client_for(self.ceo), 'not-a-uuid', make_op('customer', 'create', {'name': 'Z'}))
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_device_falls_back_to_the_old_user_based_branch(self):
        seller, shop = make_shop_user('una', 'Solo Shop')
        op = make_op('customer', 'create', {'name': 'Kim'})
        response, results = push_from(client_for(seller), uuid.uuid4(), op)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(results[op['id']]['status'], 'applied')
        self.assertTrue(Customer.objects.filter(id=op['entity_id'], shop=shop).exists())

    def test_pull_and_reconcile_are_scoped_to_the_tills_branch(self):
        Customer.objects.create(shop=self.branch_a, name='Only in A')
        in_b = Customer.objects.create(shop=self.branch_b, name='Only in B')
        client = client_for(self.ceo)
        headers = {'HTTP_X_DEVICE_ID': str(self.till_b.id)}

        pulled = client.get(PULL_URL, {'entity_types': 'customer'}, **headers)
        self.assertEqual(pulled.status_code, 200)
        self.assertEqual([op['entity_id'] for op in pulled.json()['operations']], [str(in_b.id)])

        counts = client.get(RECONCILE_URL, **headers)
        self.assertEqual(counts.status_code, 200)
        self.assertEqual(counts.json()['counts']['customer']['total'], 1)


class ReusedOperationIdTests(TestCase):
    """G6."""

    def setUp(self):
        self.user, self.shop = make_shop_user('rita', 'Shop R')
        self.client_ = client_for(self.user)

    def test_an_exact_retry_is_already_applied_and_the_hash_is_stored(self):
        op = make_op('customer', 'create', {'name': 'Same'})
        push(self.client_, op)
        _, results = push(self.client_, op)

        self.assertEqual(results[op['id']]['status'], 'already_applied')
        self.assertEqual(len(SyncOperation.objects.get(id=op['id']).payload_hash), 64)
        self.assertEqual(Customer.objects.filter(shop=self.shop).count(), 1)

    def test_a_retry_sent_at_a_different_time_is_still_the_same_operation(self):
        op = make_op('customer', 'create', {'name': 'Same'})
        push(self.client_, op)
        later = dict(op, client_timestamp=(timezone.now() + timedelta(hours=1)).isoformat())
        _, results = push(self.client_, later)
        self.assertEqual(results[op['id']]['status'], 'already_applied')

    def test_the_same_id_with_different_content_is_rejected_and_changes_nothing(self):
        op = make_op('customer', 'create', {'name': 'Original'})
        push(self.client_, op)
        changed = dict(op, payload={'name': 'Something else'})
        _, results = push(self.client_, changed)

        self.assertEqual(results[op['id']]['status'], 'rejected')
        self.assertIn('different content', results[op['id']]['error'])
        self.assertEqual(Customer.objects.get(id=op['entity_id']).name, 'Original')

    def test_the_same_id_for_a_different_entity_is_rejected(self):
        op = make_op('customer', 'create', {'name': 'Original'})
        push(self.client_, op)
        _, results = push(self.client_, dict(op, entity_id=str(uuid.uuid4())))
        self.assertEqual(results[op['id']]['status'], 'rejected')

    def test_rows_recorded_before_hashes_existed_are_still_trusted(self):
        op = make_op('customer', 'create', {'name': 'Legacy'})
        SyncOperation.objects.create(
            id=op['id'], shop=self.shop, entity_type='customer', entity_id=op['entity_id'], operation='create',
        )
        _, results = push(self.client_, op)
        self.assertEqual(results[op['id']]['status'], 'already_applied')


class PullCursorOverlapTests(TestCase):
    """G4."""

    def setUp(self):
        self.user, self.shop = make_shop_user('pat', 'Shop P')
        self.client_ = client_for(self.user)

    def pull(self, since=None):
        params = {'entity_types': 'customer'}
        if since:
            params['since'] = since
        response = self.client_.get(PULL_URL, params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_next_since_reaches_back_behind_the_read(self):
        body = self.pull()
        server_time = parse_datetime(body['server_time'])
        next_since = parse_datetime(body['next_since'])
        self.assertLessEqual(next_since, server_time - PULL_CURSOR_OVERLAP)

    def test_a_row_committed_after_the_pull_read_is_delivered_by_the_next_pull(self):
        read_at = timezone.now()
        with mock.patch('sync.views.timezone.now', return_value=read_at):
            first = self.pull()
        self.assertEqual(first['operations'], [])

        # A row that was SAVED before the first pull read the table (so its
        # updated_at is earlier) but only COMMITTED afterwards, so that pull
        # could not see it. QuerySet.update() sets updated_at exactly.
        late = Customer.objects.create(shop=self.shop, name='Late commit')
        Customer.objects.filter(pk=late.pk).update(updated_at=read_at - timedelta(seconds=30))

        old_cursor = self.pull(since=first['server_time'])
        self.assertEqual(old_cursor['operations'], [], 'the old cursor really does skip it (the G4 bug)')

        new_cursor = self.pull(since=first['next_since'])
        self.assertEqual([op['entity_id'] for op in new_cursor['operations']], [str(late.pk)])
