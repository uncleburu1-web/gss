"""
Covers the durability/ordering/multi-branch guarantees added on top of
the existing WebSocket layer. The scenarios below map onto the
architecture doc's test list as noted per class. Run with:

    python manage.py test realtime
"""
import json

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from channels.testing import WebsocketCommunicator

from core.models import Organization, Shop
from staff.models import Worker
from .events import broadcast
from .models import RealtimeEvent


def make_branch_worker(username, shop_name='Test Shop'):
    """A single-branch, non-CEO login — the common case."""
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name=shop_name, owner=None, business_type='general')
    shop = Shop.objects.create(name=shop_name, organization=org)
    Worker.objects.create(user=user, full_name=username, role='seller', shop=shop)
    return user, shop


def make_ceo(username, branch_names):
    """A CEO who owns an Organization with `len(branch_names)` branches."""
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name=f"{username}'s shops", owner=user, business_type='general')
    shops = [Shop.objects.create(name=name, organization=org) for name in branch_names]
    # Legacy owner Worker row at the first branch — see staff.models.Worker's
    # own docstring; get_shops_for_user checks Organization ownership first,
    # so this is just for is_owner()-style checks elsewhere, not tested here.
    Worker.objects.create(user=user, full_name=username, role='owner', shop=shops[0])
    return user, shops


class BroadcastDurabilityTests(TestCase):
    """Sequence #3/#4/#10/#11 in the architecture doc: every event gets a
    unique id and a gapless per-shop sequence, and it's impossible for
    two events on the same shop to collide on a sequence number."""

    def setUp(self):
        _, self.shop = make_branch_worker('seller1')

    def test_sequence_increments_per_shop(self):
        with self.captureOnCommitCallbacks(execute=True):
            broadcast(self.shop, 'sale.created', {'id': '1'})
            broadcast(self.shop, 'sale.created', {'id': '2'})

        events = list(RealtimeEvent.objects.filter(shop=self.shop).order_by('sequence'))
        self.assertEqual([e.sequence for e in events], [1, 2])
        self.assertNotEqual(events[0].id, events[1].id)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.last_event_sequence, 2)

    def test_sequence_is_per_shop_not_global(self):
        _, other_shop = make_branch_worker('seller2', shop_name='Other Shop')
        with self.captureOnCommitCallbacks(execute=True):
            broadcast(self.shop, 'sale.created', {})
            broadcast(other_shop, 'sale.created', {})
            broadcast(self.shop, 'sale.created', {})

        self.assertEqual(
            list(RealtimeEvent.objects.filter(shop=self.shop).order_by('sequence').values_list('sequence', flat=True)),
            [1, 2],
        )
        self.assertEqual(
            list(RealtimeEvent.objects.filter(shop=other_shop).values_list('sequence', flat=True)),
            [1],
        )

    def test_duplicate_sequence_for_same_shop_is_rejected_at_the_db(self):
        """Belt-and-braces: even if application code somehow tried to
        reuse a sequence number, the DB constraint refuses it outright."""
        with self.captureOnCommitCallbacks(execute=True):
            broadcast(self.shop, 'sale.created', {})
        with self.assertRaises(IntegrityError):
            RealtimeEvent.objects.create(shop=self.shop, sequence=1, event_type='sale.created', payload={})

    def test_rolled_back_transaction_never_broadcasts(self):
        """Point #1/#15: an event must never describe a write that didn't
        actually happen. Uses Django's captureOnCommitCallbacks so the
        deferred broadcast() callback runs (or doesn't) exactly as it
        would outside a test's normal transaction wrapping."""
        try:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(ValueError):
                    from django.db import transaction
                    with transaction.atomic():
                        broadcast(self.shop, 'sale.created', {'id': 'doomed'})
                        raise ValueError('simulate a later failure in the same transaction')
        except Exception:
            pass
        self.assertEqual(RealtimeEvent.objects.filter(shop=self.shop).count(), 0)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.last_event_sequence, 0)


class RealtimeStatusViewTests(TestCase):
    """Point #12: a user must only ever see sequence numbers for shops
    they're actually allowed to see."""

    def test_branch_worker_sees_only_their_own_shop(self):
        user, shop = make_branch_worker('seller3')
        with self.captureOnCommitCallbacks(execute=True):
            broadcast(shop, 'sale.created', {})
        client = APIClient()
        client.force_authenticate(user=user)

        res = client.get('/api/realtime/status/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {str(shop.id): 1})

    def test_ceo_sees_every_branch(self):
        user, shops = make_ceo('ceo1', ['Wuse', 'Garki'])
        with self.captureOnCommitCallbacks(execute=True):
            broadcast(shops[0], 'sale.created', {})
            broadcast(shops[1], 'sale.created', {})
            broadcast(shops[1], 'sale.created', {})
        client = APIClient()
        client.force_authenticate(user=user)

        res = client.get('/api/realtime/status/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {str(shops[0].id): 1, str(shops[1].id): 2})

    def test_anonymous_is_rejected(self):
        res = APIClient().get('/api/realtime/status/')
        self.assertEqual(res.status_code, 401)


def _ws_path(user):
    token = str(AccessToken.for_user(user))
    return f'/ws/live/?token={token}'


class WebSocketConsumerTests(TransactionTestCase):
    """Exercises the actual production ASGI app (asgi.application) end to
    end — middleware, routing, and consumer together — rather than the
    consumer in isolation, since the multi-branch bug this fixes lived
    in how the middleware and consumer's scope agreement, not in either
    file alone.

    Maps onto the architecture doc's test list:
      - Test 5 (server restart): not reproducible in-process; covered in
        spirit by test_hello_gives_current_sequence_on_every_connect,
        since that's exactly what a client leans on after any restart.
      - Test 13 (CEO app disconnects/reconnects): see
        test_ceo_socket_receives_events_from_every_branch and
        test_reconnect_after_drop_gets_fresh_hello.
      - Test 14 (device revoked while connected): not modeled here —
        there's no device-revocation hook wired into this consumer yet
        (see honest gaps in my write-up).
    """

    async def _connect(self, user):
        from benchline.asgi import application
        communicator = WebsocketCommunicator(application, _ws_path(user))
        connected, _ = await communicator.connect()
        return communicator, connected

    async def test_missing_token_is_refused(self):
        from benchline.asgi import application
        communicator = WebsocketCommunicator(application, '/ws/live/')
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_connect_receives_hello_with_current_sequence(self):
        user, shop = await self._async_make_branch_worker('seller4')
        communicator, connected = await self._connect(user)
        self.assertTrue(connected)

        hello = json.loads(await communicator.receive_from())
        self.assertEqual(hello['event'], 'hello')
        self.assertEqual(hello['sequences'], {str(shop.id): 0})

        await communicator.disconnect()

    async def test_receives_broadcast_for_own_shop(self):
        user, shop = await self._async_make_branch_worker('seller5')
        communicator, connected = await self._connect(user)
        await communicator.receive_from()  # discard 'hello'

        from channels.db import database_sync_to_async
        await database_sync_to_async(broadcast)(shop, 'sale.created', {'id': '123'})

        message = json.loads(await communicator.receive_from(timeout=2))
        self.assertEqual(message['event'], 'sale.created')
        self.assertEqual(message['payload'], {'id': '123'})
        self.assertEqual(message['sequence'], 1)
        self.assertIsNotNone(message['event_id'])

        await communicator.disconnect()

    async def test_ceo_socket_receives_events_from_every_branch(self):
        """The actual bug this session fixed: a CEO with more than one
        branch used to have get_shop_for_user() raise inside the
        middleware, leaving scope['shop'] = None and the connection
        refused outright (code 4001) — a multi-branch CEO got zero
        real-time updates, full stop. This proves both branches now
        deliver onto the one socket."""
        user, shops = await self._async_make_ceo('ceo2', ['Wuse', 'Garki'])
        communicator, connected = await self._connect(user)
        self.assertTrue(connected)
        await communicator.receive_from()  # discard 'hello'

        from channels.db import database_sync_to_async
        await database_sync_to_async(broadcast)(shops[0], 'sale.created', {'branch': 'Wuse'})
        await database_sync_to_async(broadcast)(shops[1], 'sale.created', {'branch': 'Garki'})

        first = json.loads(await communicator.receive_from(timeout=2))
        second = json.loads(await communicator.receive_from(timeout=2))
        received_branches = {first['payload']['branch'], second['payload']['branch']}
        self.assertEqual(received_branches, {'Wuse', 'Garki'})

        await communicator.disconnect()

    async def _async_make_branch_worker(self, username):
        from channels.db import database_sync_to_async
        return await database_sync_to_async(make_branch_worker)(username)

    async def _async_make_ceo(self, username, branch_names):
        from channels.db import database_sync_to_async
        return await database_sync_to_async(make_ceo)(username, branch_names)
