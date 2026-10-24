"""Give core_shop.shop_type a database default (''), so code from before the column existed (a
rolled-back deploy) can still insert branches. Nothing else changes; reversing drops the default."""
from django.db import migrations, models

SHOP_TYPE_CHOICES = [
    ('gadgets', 'Gadgets & electronics (phones, laptops, accessories)'),
    ('clothing', 'Clothing & fashion'),
    ('pharmacy', 'Pharmacy & health products'),
    ('restaurant', 'Restaurant & food (restaurant, café, bar, fast food)'),
    ('general', 'General retail / other'),
]


class Migration(migrations.Migration):
    dependencies = [('core', '0012_shop_type')]
    operations = [
        migrations.AlterField(
            model_name='shop',
            name='shop_type',
            field=models.CharField(
                blank=True, choices=SHOP_TYPE_CHOICES, db_default='', default='', max_length=20,
                help_text="What kind of shop this branch is (gadgets, pharmacy, restaurant, ...). Left empty, it "
                          "takes the business's sign-up type when saved. Changing it changes the branch's modules "
                          "(branches.views.BranchViewSet.perform_update).",
            ),
        ),
    ]
