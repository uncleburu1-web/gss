from django.conf import settings
from django.db import models

from core.models import Shop


class SyncOperation(models.Model):
    """One row per outbox operation the cloud has ever accepted from a
    desktop, keyed on the *operation* id the desktop generated (not the
    entity id — a product can be created once but updated many times, each
    with its own operation id). This is the entire idempotency mechanism:
    if a push is retried (e.g. the desktop got a network timeout right as
    the cloud's response was on its way back), replaying the same
    operation id here is detected and reported as `already_applied`
    instead of being double-applied.

    Deliberately NOT a SyncModel itself — this table is cloud-only
    bookkeeping about sync, not shop data that itself needs to sync
    anywhere.
    """
    id = models.UUIDField(primary_key=True, editable=False)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='sync_operations')
    entity_type = models.CharField(max_length=40)
    entity_id = models.UUIDField()
    operation = models.CharField(max_length=10)
    applied_at = models.DateTimeField(auto_now_add=True)
    # Who sent it and from where. Both nullable: rows recorded before these
    # columns existed have neither, and web/Android never push through here.
    device = models.ForeignKey(
        'devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='sync_operations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    payload_hash = models.CharField(
        max_length=64, blank=True, default='',
        help_text='sha256 of the operation as first applied (entity_type, entity_id, operation, payload). '
                  'A later push reusing this id with DIFFERENT content is rejected instead of being '
                  'silently reported as already applied. Empty on rows recorded before this existed.',
    )

    class Meta:
        indexes = [models.Index(fields=['shop', 'entity_type', 'entity_id'])]

    def __str__(self):
        return f'{self.entity_type}:{self.operation} {self.entity_id}'
