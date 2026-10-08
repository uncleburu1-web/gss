# Barcode uniqueness: global -> per branch.
#
# InventoryItem.barcode used to be `unique=True`, i.e. unique across every
# shop in the database, so one shop could block another (or a sibling branch
# of the same business) from using a barcode. This drops that column-level
# constraint and replaces it with a partial unique constraint on
# (shop, barcode) that ignores soft-deleted rows and barcode-less products.
#
# Safe on existing data: if every barcode was unique globally, it is by
# definition unique per branch, so adding the narrower constraint cannot fail.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_inventoryitem_image_url_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventoryitem',
            name='barcode',
            field=models.CharField(
                blank=True, db_index=True, max_length=64, null=True,
                help_text='Scanned/printed barcode (UPC/EAN or a shop-assigned code). Unique per branch only (see '
                           'Meta.constraints) -- other branches and other shops may reuse the same code. Null (not '
                           'empty string) when unset.',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventoryitem',
            constraint=models.UniqueConstraint(
                fields=('shop', 'barcode'),
                condition=models.Q(is_deleted=False, barcode__isnull=False) & ~models.Q(barcode=''),
                name='unique_barcode_per_branch',
            ),
        ),
    ]
