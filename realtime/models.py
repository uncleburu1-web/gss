import uuid

from django.db import models


class RealtimeEvent(models.Model):
    """Durable log backing the WebSocket layer's real-time notifications
    (see events.py). Two jobs:

    1. Gives every event a stable identity (id) and a per-shop total
       order (sequence), so a client that missed messages — a dropped
       connection, a server restart, a channel-layer hiccup — can tell
       exactly whether it missed anything and ask for a resync, instead
       of just hoping reconnecting caught it up.
    2. Makes the notification itself durable: broadcast() persists this
       row BEFORE it touches the channel layer, so if the channel layer
       is unreachable (Redis down, etc.) the event still exists — a
       client checking in later still sees that something happened,
       even though nothing went out live at the time.

    This is NOT the source of truth for business data (Sale, StockBatch,
    etc. still are, same as always) — it's the source of truth for
    "which notifications were sent," which a fire-and-forget WebSocket
    message can never provide on its own. See core.Shop.last_event_sequence
    for how `sequence` is assigned.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('core.Shop', on_delete=models.CASCADE, related_name='realtime_events')
    sequence = models.BigIntegerField(help_text='Per-shop monotonic order — see core.Shop.last_event_sequence.')
    event_type = models.CharField(max_length=50, help_text='e.g. "sale.created", "inventory.updated".')
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['shop', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['shop', 'sequence'], name='unique_shop_event_sequence'),
        ]
        indexes = [
            models.Index(fields=['shop', 'sequence']),
            # Backs the retention cleanup (management command) and any
            # "how far back does our log even go" check during reconciliation.
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'{self.event_type} #{self.sequence} ({self.shop_id})'
