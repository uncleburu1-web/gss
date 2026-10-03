# Preserves history across the pending/paid -> unpaid/partially_paid/cleared
# change in 0002. Nothing here is a guess about money: every existing row's
# `amount` is untouched, and a "paid" liability gets exactly one
# LiabilityPayment for its full original amount, so amount_paid/outstanding
# (both derived from payments — see Liability.amount_paid) come out
# identical to what the old flat status implied. What genuinely can't be
# recovered is the original payment METHOD and the exact payment DATE (the
# old model never recorded either) — payment_method defaults to 'cash' and
# paid_at uses the liability's own updated_at as the closest available
# proxy for "when it was marked paid," and the note on each backfilled
# payment says plainly that these are reconstructed, not original, values.
from django.db import migrations


def backfill_payment_history(apps, schema_editor):
    Liability = apps.get_model('liabilities', 'Liability')
    LiabilityPayment = apps.get_model('liabilities', 'LiabilityPayment')

    for liability in Liability.objects.all():
        if liability.status == 'paid':
            LiabilityPayment.objects.create(
                shop_id=liability.shop_id,
                liability=liability,
                amount=liability.amount,
                payment_method='cash',
                paid_at=liability.updated_at,
                notes='Reconstructed from this liability\u2019s legacy "paid" status — '
                      'original payment method/date were not recorded.',
            )
            liability.status = 'cleared'
            liability.cleared_at = liability.updated_at
        else:
            # Only other legacy value was 'pending'.
            liability.status = 'unpaid'
        liability.save(update_fields=['status', 'cleared_at'])


def noop_reverse(apps, schema_editor):
    # Deliberately does not delete the backfilled payments or revert
    # status on reverse — undoing a migration must never look like it's
    # allowed to destroy real financial history.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('liabilities', '0002_liabilitypayment_liability_cleared_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_payment_history, noop_reverse),
    ]
