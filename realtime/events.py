"""
The entire real-time layer in one idea: broadcast() is called AFTER data
is already safely committed to Postgres (by whatever HTTP view or sync
handler just did it) — it just nudges anyone connected to go look. If
nobody's listening, if the channel layer is down, if this call silently
no-ops for any reason, NOTHING is lost, because nothing here is data.

This is the CAP-theorem-aware design the architecture asks for: the
desktop (and every HTTP endpoint) stays available even when this layer
is completely unreachable — a network partition here degrades to "no
live updates," never to "can't sell" or "lost a sale." Consistency
between clients is eventual, driven by the same durable HTTP fetch every
page already does — this just makes "eventual" happen in under a second
instead of whenever someone next hits refresh.

Two things layered on top of that original design, both in models.py's
RealtimeEvent:

1. DURABILITY: every event is persisted before it's sent. If the channel
   layer is unreachable at broadcast time, the notification itself is
   lost (same as before), but the fact that something happened is not —
   a client that checks in later (see views.RealtimeStatusView and the
   consumer's 'hello' message) can still tell it missed something.
2. ORDERING: each persisted event gets a gapless, per-shop sequence
   number (core.Shop.last_event_sequence). A client that knows "I last
   saw sequence 41 for this shop" and is now told the shop is at 44
   knows unambiguously that it missed 42 and 43, without needing
   timestamps (clock skew across devices makes those unreliable for
   this) or trusting that a reconnect alone means it caught up.

broadcast() is also deferred until the enclosing transaction actually
commits (transaction.on_commit) — it's normally called as the last line
of an @transaction.atomic view method, which means the write it
describes hasn't actually hit disk yet at the point broadcast() runs.
Without this, a client could receive the notification, refetch, and
still not see the change (or worse, see a half-committed state), or the
whole notification could vanish if something later in the same
transaction rolled it back. Deferring closes that gap without requiring
any of the existing call sites to change — if there's no active
transaction, Django just runs it immediately, so this is always safe to
call exactly as before.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def shop_group_name(shop_id):
    return f'shop_{shop_id}'


def broadcast(shop, event_type, payload):
    """event_type examples: 'sale.created', 'product.updated',
    'product.created', 'customer.updated'. `payload` should be small and
    JSON-safe — a hint for the UI (e.g. which product changed), not a
    full authoritative record; the client re-fetches for the real data.
    """
    def _send():
        # Imported here rather than at module level purely to keep this
        # module importable before app-loading finishes in places like
        # asgi.py that touch it very early — see that file's own comment
        # about import order.
        from core.models import Shop
        from .models import RealtimeEvent

        with transaction.atomic():
            locked_shop = Shop.objects.select_for_update().get(pk=shop.pk)
            locked_shop.last_event_sequence += 1
            locked_shop.save(update_fields=['last_event_sequence'])
            event = RealtimeEvent.objects.create(
                shop=locked_shop,
                sequence=locked_shop.last_event_sequence,
                event_type=event_type,
                payload=payload,
            )

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            shop_group_name(shop.id),
            {
                'type': 'shop.event',
                'event': event_type,
                'payload': payload,
                'event_id': str(event.id),
                'sequence': event.sequence,
                'shop_id': str(shop.id),
            },
        )

    transaction.on_commit(_send)
