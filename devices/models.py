from datetime import timedelta
from django.db import models
from django.utils import timezone
from core.models import SyncModel

# How stale a heartbeat can be before we consider the desktop "offline" from
# the cloud's point of view. There's no persistent connection to check —
# this timestamp aging out is the only signal we have.
ONLINE_THRESHOLD = timedelta(minutes=2)


class Device(SyncModel):
    DEVICE_TYPE_CHOICES = [
        ('desktop', 'Desktop POS'),
        ('android', 'Android CEO app'),
    ]

    device_type = models.CharField(max_length=10, choices=DEVICE_TYPE_CHOICES)
    name = models.CharField(max_length=150, blank=True, help_text='e.g. "Front counter PC", "CEO\'s phone"')
    app_version = models.CharField(max_length=30, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f'{self.name or self.device_type} ({self.shop.name})'

    @property
    def is_online(self):
        if not self.last_seen_at:
            return False
        return timezone.now() - self.last_seen_at <= ONLINE_THRESHOLD
