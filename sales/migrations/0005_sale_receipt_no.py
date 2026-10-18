from django.db import migrations, models


class Migration(migrations.Migration):
    """One new nullable-by-default column plus an index. Existing sales keep an empty receipt number."""

    dependencies = [
        ('sales', '0004_alter_sale_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='receipt_no',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=40,
                help_text='What the till printed: <till code>-<counter>, e.g. "A3F9C2-000123". Unique BY CONSTRUCTION '
                          'on each till (the code differs per till, the counter never repeats), which the plain '
                          'per-till invoice_number is not. Deliberately NOT a unique constraint here: rejecting a '
                          'sale over a numbering collision would lose a real sale for a cosmetic reason.',
            ),
        ),
    ]
