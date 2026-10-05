"""
Stage 0 regression tests for the offline-sync push path.

Each test encodes one verified defect from the architecture audit
(docs: Benchline-Core-Engine-Analysis.md, section 1.4) so it can never come
back silently.  Run with:

    python manage.py test sync
"""
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from core.models import Organization, Shop
from customers.models import Customer
from realtime import events as realtime_events
from realtime.models import RealtimeEvent
from sales.models import Sale
from staff.models import Worker

from .models import SyncOperation

PUSH_URL = '/api/sync/push/'


def make_shop_user(username, shop_name):
    """A single-branch, non-CEO login (same shape realtime/tests.py uses)."""
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name=shop_name, owner=None, business_type='general')
    shop = Shop.objects.create(name=shop_name, organization=org)
    Worker.objects.create(user=user, full_name=username, role='seller', shop=shop)
    return user, shop


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_op(entity_type, operation, payload, op_id=None, entity_id=None):
    return {
        'id': str(op_id or uuid.uuid4()),
        'entity_type': entity_type,
        'entity_id': str(entity_id or uuid.uuid4()),
        'operation': operation,
        'payload': payload,
        'client_timestamp': timezone.now().isoformat(),
    }


def sale_payload(date_iso=None, **extra):
    payload = {
        'customer_name': 'Walk-in', 'staff_name': 'tester', 'payment_method': 'cash',
        'status': 'completed', 'amount_paid': 0, 'invoice_number': 1,
    }
    if date_iso is not None:
        payload['date'] = date_iso
    payload.update(extra)
    return payload


def push(client, *operations):
    response = client.post(PUSH_URL, {'operations': list(operations)}, format='json')
    return response, {r['id']: r for r in response.json().get('results', [])}


class IdempotencyTests(TestCase):
    """Audit findings D-05 / D-20: duplicate pushes must never double-apply,
    and one shop's operation ids must be invisible to every other shop."""

    def setUp(self):
        self.user_a, self.shop_a = make_shop_user('alice', 'Shop A')
        self.user_b, self.shop_b = make_shop_user('bob', 'Shop B')

    def test_replayed_operation_is_already_applied_and_not_duplicated(self):
        operation = make_op('customer', 'create', {'name': 'Ada'})
        client = client_for(self.user_a)

        _, first = push(client, operation)
        _, second = push(client, operation)

        self.assertEqual(first[operation['id']]['status'], 'applied')
        self.assertEqual(second[operation['id']]['status'], 'already_applied')
        self.assertEqual(Customer.objects.filter(shop=self.shop_a).count(), 1)

    def test_operation_ids_are_scoped_to_the_shop(self):
        shared_id = uuid.uuid4()
        push(client_for(self.user_a), make_op('customer', 'create', {'name': 'A-only'}, op_id=shared_id))

        _, results = push(client_for(self.user_b), make_op('customer', 'create', {'name': 'B-try'}, op_id=shared_id))

        # Shop B must NOT be told "already applied" (that would leak that the id
        # exists elsewhere AND silently drop B's write). It is rejected, loudly.
        self.assertEqual(results[str(shared_id)]['status'], 'rejected')
        self.assertFalse(Customer.objects.filter(shop=self.shop_b).exists())

    def test_losing_a_concurrent_claim_is_reported_as_already_applied(self):
        """Simulates the race: the pre-check says 'not applied yet', but another
        request has inserted the SyncOperation row by the time we claim it."""
        operation = make_op('customer', 'create', {'name': 'Race'})
        SyncOperation.objects.create(
            id=operation['id'], shop=self.shop_a, entity_type='customer',
            entity_id=operation['entity_id'], operation='create',
        )

        with mock.patch('sync.views._already_applied', side_effect=[False, True]):
            _, results = push(client_for(self.user_a), operation)

        self.assertEqual(results[operation['id']]['status'], 'already_applied')
        # The handler must not have run a second time.
        self.assertFalse(Customer.objects.filter(shop=self.shop_a).exists())

    def test_rejected_operation_is_not_recorded_so_a_fixed_retry_can_apply(self):
        bad = make_op('customer', 'create', {'name': 'x'})
        bad['entity_type'] = 'not_a_real_entity'
        _, results = push(client_for(self.user_a), bad)
        self.assertEqual(results[bad['id']]['status'], 'rejected')
        self.assertIn('unknown entity_type', results[bad['id']]['error'])
        self.assertFalse(SyncOperation.objects.filter(id=bad['id']).exists())


class SaleDateTests(TestCase):
    """Audit finding D-01: an offline sale must keep the time it was RUNG UP,
    not the time it finally reached the server."""

    def setUp(self):
        self.user, self.shop = make_shop_user('carol', 'Shop C')
        self.client_ = client_for(self.user)

    def test_offline_sale_keeps_its_own_date(self):
        rung_up = (timezone.now() - timedelta(days=3)).isoformat()
        operation = make_op('sale', 'create', sale_payload(rung_up))
        push(self.client_, operation)

        sale = Sale.objects.get(id=operation['entity_id'])
        self.assertLess(abs(sale.date - parse_datetime(rung_up)), timedelta(seconds=1))

    def test_a_date_in_the_future_is_not_trusted(self):
        tomorrow = (timezone.now() + timedelta(days=1)).isoformat()
        operation = make_op('sale', 'create', sale_payload(tomorrow))
        push(self.client_, operation)

        self.assertLessEqual(Sale.objects.get(id=operation['entity_id']).date, timezone.now())

    def test_a_missing_or_garbage_date_falls_back_to_arrival_time(self):
        for bad in (None, 'not-a-date', 12345):
            operation = make_op('sale', 'create', sale_payload(None, date=bad) if bad is not None else sale_payload())
            push(self.client_, operation)
            sale = Sale.objects.get(id=operation['entity_id'])
            self.assertLess(abs(timezone.now() - sale.date), timedelta(minutes=1))

    def test_a_later_update_cannot_rewrite_the_date(self):
        original = (timezone.now() - timedelta(days=2)).isoformat()
        entity_id = uuid.uuid4()
        push(self.client_, make_op('sale', 'create', sale_payload(original), entity_id=entity_id))

        forged = (timezone.now() - timedelta(days=30)).isoformat()
        push(self.client_, make_op('sale', 'update', sale_payload(forged, amount_paid=5), entity_id=entity_id))

        sale = Sale.objects.get(id=entity_id)
        self.assertLess(abs(sale.date - parse_datetime(original)), timedelta(seconds=1))


class _BrokenLayer:
    async def group_send(self, *args, **kwargs):
        raise ConnectionError('redis is down')


class NotificationFailureTests(TestCase):
    """Audit finding D-03: a dead channel layer must never turn a committed
    write into an error response."""

    def setUp(self):
        self.user, self.shop = make_shop_user('dave', 'Shop D')

    def test_push_succeeds_when_the_channel_layer_is_down(self):
        operation = make_op('customer', 'create', {'name': 'Still saved'})

        with mock.patch.object(realtime_events, 'get_channel_layer', return_value=_BrokenLayer()):
            # TestCase wraps everything in a transaction, so on_commit callbacks
            # only run when we ask for them -- which is exactly the moment the
            # old code would have raised.
            with self.captureOnCommitCallbacks(execute=True):
                response, results = push(client_for(self.user), operation)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(results[operation['id']]['status'], 'applied')
        self.assertTrue(Customer.objects.filter(shop=self.shop, name='Still saved').exists())

    def test_the_event_is_still_persisted_when_only_the_send_fails(self):
        with mock.patch.object(realtime_events, 'get_channel_layer', return_value=_BrokenLayer()):
            with self.captureOnCommitCallbacks(execute=True):
                realtime_events.broadcast(self.shop, 'product.updated', {'id': 'x'})

        # Clients that reconnect later can still learn they missed something.
        self.assertEqual(RealtimeEvent.objects.filter(shop=self.shop).count(), 1)
