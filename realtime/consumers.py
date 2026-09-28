import asyncio
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from .events import shop_group_name

logger = logging.getLogger(__name__)


class ShopEventsConsumer(AsyncWebsocketConsumer):
    """One connection per logged-in browser tab / device, joined to the
    broadcast group of every shop this user can see (a branch-scoped
    worker: their one shop; a CEO: every branch in their org, or just
    one if ?branch=<id> was given on the connection URL — see
    middleware.py). Deliberately light on business logic: no state
    beyond "which groups is this socket in" and a heartbeat timer,
    nothing persisted per-connection. If this connection drops, nothing
    is lost — the client's own reconnect+reconcile logic (the 'hello'
    message below, and GET /api/realtime/status/) is what's actually
    authoritative; this only exists so most updates arrive well under a
    second instead of whenever the client's next poll/refetch happens
    to run.

    Every important business transaction still goes through the normal
    REST API and its own permission checks — this consumer never
    accepts anything from a client that could create or change data
    (see receive() below); a WebSocket message can only ever be a
    heartbeat reply.
    """

    # Overridable per-instance in tests, which need this far shorter
    # than a real 30s wait to run in reasonable time.
    HEARTBEAT_INTERVAL_SECONDS = 30

    async def connect(self):
        shops = self.scope.get('shops') or []
        user = self.scope.get('user')
        if not shops or user is None or not getattr(user, 'is_authenticated', False):
            await self.close(code=4001)
            return

        self.group_names = [shop_group_name(shop.id) for shop in shops]
        for group_name in self.group_names:
            await self.channel_layer.group_add(group_name, self.channel_name)
        await self.accept()

        # An immediate, authoritative sequence baseline for every shop
        # this socket can see — sent on first connect AND every
        # reconnect. The client compares this against whatever it last
        # saw for each shop; a gap means "something happened while I
        # was away," and the client's response is the same refetch a
        # live event would have triggered. This is what makes
        # reconnection something more than a hope that nothing was missed.
        sequences = {str(shop.id): shop.last_event_sequence for shop in shops}
        await self.send(text_data=json.dumps({'event': 'hello', 'sequences': sequences}))

        self._last_pong_at = None
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    async def disconnect(self, close_code):
        task = getattr(self, '_heartbeat_task', None)
        if task is not None:
            task.cancel()
        for group_name in getattr(self, 'group_names', []):
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # The only legitimate client-originated message on this channel
        # is a heartbeat reply — see the module docstring on why nothing
        # else is accepted here. Anything malformed or unrecognized is
        # silently ignored rather than erroring the connection closed;
        # a stray/garbled frame from a flaky connection shouldn't cost
        # the client its whole session.
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return
        if isinstance(data, dict) and data.get('type') == 'pong':
            self._last_pong_at = asyncio.get_event_loop().time()

    async def _heartbeat_loop(self):
        """Distinguishes "the WebSocket is technically still open" from
        "this connection is actually alive and talking to us." Some
        networks (NAT timeouts, certain mobile carriers, a laptop that
        went to sleep) can leave a socket looking connected to the OS
        long after nothing is actually getting through in either
        direction — sometimes well before onclose ever fires. A client
        that stops hearing pings can decide to give up and reconnect on
        its own terms instead of waiting indefinitely on a dead socket.
        """
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
                await self.send(text_data=json.dumps({'event': 'ping'}))
        except asyncio.CancelledError:
            pass
        except Exception:
            # A send failure here means the connection is already on its
            # way out — disconnect() will run and cancel this either
            # way; nothing useful is served by letting this crash loudly.
            logger.debug('heartbeat loop ending', exc_info=True)

    async def shop_event(self, event):
        # group_send sends {'type': 'shop.event', ...} — Channels maps
        # 'shop.event' to this method name (dots -> underscores).
        await self.send(text_data=json.dumps({
            'event': event['event'],
            'payload': event['payload'],
            'event_id': event.get('event_id'),
            'sequence': event.get('sequence'),
            'shop_id': event.get('shop_id'),
        }))
