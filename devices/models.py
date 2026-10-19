from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone
from core.models import SyncModel

# How stale a heartbeat can be before we consider the desktop "offline" from
# the cloud's point of view. There's no persistent connection to check —
# this timestamp aging out is the only signal we have.
ONLINE_THRESHOLD = timedelta(minutes=2)
# No contact at all for this long = OFFLINE. Between ONLINE_THRESHOLD and this = STALE.
OFFLINE_THRESHOLD = timedelta(minutes=15)
# A till that IS in contact but has had changes waiting longer than this without a successful
# upload is STALE: "the till is on, but its sales are not reaching you".
SYNC_STUCK_AFTER = timedelta(minutes=5)


class Device(SyncModel):
    DEVICE_TYPE_CHOICES = [
        ('desktop', 'Desktop POS'),
        ('android', 'Android CEO app'),
    ]

    device_type = models.CharField(max_length=10, choices=DEVICE_TYPE_CHOICES)
    name = models.CharField(max_length=150, blank=True, help_text='e.g. "Front counter PC", "CEO\'s phone"')
    app_version = models.CharField(max_length=30, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Stage 1b: what the cloud knows about this till's health, so ONLINE/STALE/OFFLINE is decided
    # here, never by a UI. last_ws_seen_at comes from the live socket's pong (no extra requests);
    # last_sync_at from the last push the cloud accepted; the queue counts from the heartbeat.
    last_ws_seen_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    pending_ops = models.PositiveIntegerField(default=0)
    failed_ops = models.PositiveIntegerField(default=0)
    current_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f'{self.name or self.device_type} ({self.shop.name})'

    @property
    def last_contact_at(self):
        times = [t for t in (self.last_seen_at, self.last_ws_seen_at) if t]
        return max(times) if times else None

    @property
    def is_online(self):
        contact = self.last_contact_at
        return bool(contact and timezone.now() - contact <= ONLINE_THRESHOLD)

    @property
    def status(self):
        """'online' | 'stale' | 'offline', worked out on the server from facts the server holds.

        online  in contact in the last 2 min, nothing failed, and uploads are keeping up
        stale   in contact but uploads stuck or failing, OR last contact 2-15 min ago
        offline no contact for 15 min or more (or never)"""
        contact = self.last_contact_at
        if not contact:
            return 'offline'
        now = timezone.now()
        age = now - contact
        if age > OFFLINE_THRESHOLD:
            return 'offline'
        if age > ONLINE_THRESHOLD:
            return 'stale'
        if self.failed_ops:
            return 'stale'
        if self.pending_ops and (not self.last_sync_at or now - self.last_sync_at > SYNC_STUCK_AFTER):
            return 'stale'
        return 'online'
