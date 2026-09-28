from django.db import models
from core.models import SyncModel


class Supplier(SyncModel):
    name = models.CharField(max_length=200)
    contact_phone = models.CharField(max_length=30, blank=True)
    contact_email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['name']
        # Unique per shop, not globally — two different shops can each have
        # a supplier named "Acme".
        constraints = [
            models.UniqueConstraint(fields=['shop', 'name'], name='unique_supplier_name_per_shop'),
        ]

    def __str__(self):
        return self.name
