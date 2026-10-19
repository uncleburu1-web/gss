import uuid

from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """Who did what sensitive thing, when, and why. Append-only and kept forever.

    Written by audit.record() next to the action itself (inside the same transaction where there is
    one), so an action and its audit line exist together or not at all. Never edited, never deleted:
    retention jobs leave this table alone (see core.management.commands.prune_sync_history).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    organization = models.ForeignKey('core.Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    shop = models.ForeignKey('core.Shop', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60, db_index=True, help_text='e.g. sale.deleted, stock.damage')
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    summary = models.CharField(max_length=255, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    data = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=20, default='web', help_text='web | till | system')
    device = models.ForeignKey('devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-at']
        indexes = [models.Index(fields=['shop', 'at']), models.Index(fields=['organization', 'at'])]

    def __str__(self):
        return f'{self.at:%Y-%m-%d %H:%M} {self.actor_name or "system"}: {self.action}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Audit log entries cannot be changed.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Audit log entries cannot be deleted.')
