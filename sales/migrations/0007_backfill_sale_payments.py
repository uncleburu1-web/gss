"""One legacy SalePayment per existing sale that has money recorded, so that from now on
Sale.amount_paid == sum(payments) holds for every sale, old ones included.

Additive only: amount_paid itself is not touched (each payment equals it exactly), so every report
reads the same before and after. Ids are deterministic (sales.payments.legacy_payment_id), so a re-run
creates nothing twice and a later update from an older till adjusts this same row. Reverse: delete them.
"""
import uuid

from django.db import migrations

_LEGACY_NS = uuid.UUID('6f0b8a52-6d5e-4a3e-9a63-2b7e1c4f0d11')  # = sales.payments._LEGACY_NS
BATCH = 1000


def legacy_payment_id(sale_id):
    return uuid.uuid5(_LEGACY_NS, f'legacy:{sale_id}')


def forwards(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    SalePayment = apps.get_model('sales', 'SalePayment')
    valid_methods = {'cash', 'transfer', 'pos', 'other'}
    batch = []
    sales = Sale.objects.filter(amount_paid__gt=0).values('id', 'shop_id', 'payment_method', 'amount_paid', 'date', 'is_deleted')
    for row in sales.iterator(chunk_size=BATCH):
        batch.append(SalePayment(
            id=legacy_payment_id(row['id']), shop_id=row['shop_id'], sale_id=row['id'],
            method=row['payment_method'] if row['payment_method'] in valid_methods else 'other',
            amount=row['amount_paid'], paid_at=row['date'], source='legacy', is_deleted=row['is_deleted'],
        ))
        if len(batch) >= BATCH:
            SalePayment.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        SalePayment.objects.bulk_create(batch, ignore_conflicts=True)


def backwards(apps, schema_editor):
    apps.get_model('sales', 'SalePayment').objects.filter(source='legacy').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_salepayment_sale_device_received_at'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
