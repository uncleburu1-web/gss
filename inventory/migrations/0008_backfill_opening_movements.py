"""One OPENING movement per live batch that has stock, equal to its quantity_remaining today, so
the ledger adds up to current stock from the first day (see inventory.ledger.drift).

Additive only: no batch, sale or quantity is touched. Ids are deterministic, so a re-run creates
nothing twice. Reverse: delete the opening rows (plain queryset delete -- the model's own
append-only guard is on instance.delete(), which a migration never calls).
"""
import uuid

from django.db import migrations
from django.utils import timezone

_OPENING_NS = uuid.UUID('2b1f6d3e-8c47-4f0a-9d55-61c1e0a7b9f2')
BATCH = 1000


def opening_id(batch_id):
    return uuid.uuid5(_OPENING_NS, f'opening:{batch_id}')


def forwards(apps, schema_editor):
    StockBatch = apps.get_model('inventory', 'StockBatch')
    StockMovement = apps.get_model('inventory', 'StockMovement')
    now = timezone.now()
    rows = []
    batches = StockBatch.objects.filter(is_deleted=False, quantity_remaining__gt=0).values(
        'id', 'shop_id', 'item_id', 'variant_id', 'quantity_remaining',
    )
    for b in batches.iterator(chunk_size=BATCH):
        rows.append(StockMovement(
            id=opening_id(b['id']), shop_id=b['shop_id'], item_id=b['item_id'], variant_id=b['variant_id'],
            batch_id=b['id'], kind='opening', quantity=b['quantity_remaining'],
            reason='Stock on hand when the ledger started', source='system',
            reference_type='stock_batch', reference_id=str(b['id']), created_at=now,
        ))
        if len(rows) >= BATCH:
            StockMovement.objects.bulk_create(rows, ignore_conflicts=True)
            rows = []
    if rows:
        StockMovement.objects.bulk_create(rows, ignore_conflicts=True)


def backwards(apps, schema_editor):
    apps.get_model('inventory', 'StockMovement').objects.filter(kind='opening').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0007_stock_movement'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
