import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Sale.date: auto_now_add -> default + editable=False.

    Schema-only. No column, index or data changes (Django emits no SQL for a
    default/editable change on PostgreSQL), so this is instant and safe on a
    live table. It exists so sync can record a sale's own timestamp."""

    dependencies = [
        ('sales', '0003_saleitem_stock_shortfall'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sale',
            name='date',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
    ]
