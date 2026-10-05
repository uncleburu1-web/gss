from django.db import migrations, models


class Migration(migrations.Migration):
    """Barcode uniqueness: global -> per shop (active products only).

    Dropping the global unique index can never fail on existing data. The new
    partial unique constraint cannot fail either: a globally-unique column is
    trivially unique within every shop."""

    dependencies = [
        ('inventory', '0005_inventoryitem_image_url_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventoryitem',
            name='barcode',
            field=models.CharField(
                blank=True, db_index=True, max_length=64, null=True,
                help_text="Scanned/printed barcode (UPC/EAN or a shop-assigned code), unique among a shop's active products (see Meta.constraints). Null (not empty string) when unset.",
            ),
        ),
        migrations.AddConstraint(
            model_name='inventoryitem',
            constraint=models.UniqueConstraint(
                condition=models.Q(('barcode__isnull', False), ('is_deleted', False), models.Q(('barcode', ''), _negated=True)),
                fields=('shop', 'barcode'),
                name='uniq_item_barcode_per_shop_active',
            ),
        ),
    ]
