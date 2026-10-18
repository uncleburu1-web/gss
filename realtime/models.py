import uuid

from django.db import models


class RealtimeEvent(models.Model):
    """Durable log backing the WebSocket layer's real-time notifications.

    This records notifications so clients can detect missed events and
    request a resync. Business data remains in its original models.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    shop = models.ForeignKey(
        'core.Shop',
        on_delete=models.CASCADE,
        related_name='realtime_events',
    )

    sequence = models.BigIntegerField(
        help_text=(
            'Per-shop monotonic order — see core.Shop.last_event_sequence.'
        ),
    )

    event_type = models.CharField(
        max_length=50,
        help_text='e.g. "sale.created", "inventory.updated".',
    )

    payload = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['shop', 'sequence']

        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'sequence'],
                name='unique_shop_event_sequence',
            ),
        ]

        indexes = [
            models.Index(
                fields=['shop', 'sequence'],
                name='realtime_re_shop_id_a1b2c3_idx',
            ),
            models.Index(
                fields=['created_at'],
                name='realtime_re_created_d4e5f6_idx',
            ),
        ]

    def __str__(self):
        return f'{self.event_type} #{self.sequence} ({self.shop_id})'