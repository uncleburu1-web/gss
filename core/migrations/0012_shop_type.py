"""Each branch gets its own shop type (owner's decision 13). Every existing branch takes its business's
sign-up type, which is what it has been all along, so nothing changes for any existing shop: same
modules, same categories, same screens. Adds a column and fills it; reversing drops the column."""
from django.db import migrations, models


def copy_business_type(apps, schema_editor):
    Shop = apps.get_model('core', 'Shop')
    for shop in Shop.objects.select_related('organization').filter(shop_type=''):
        shop.shop_type = shop.organization.business_type if shop.organization_id else 'general'
        shop.save(update_fields=['shop_type'])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_default_branch_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="shop_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("gadgets", "Gadgets & electronics (phones, laptops, accessories)"),
                    ("clothing", "Clothing & fashion"),
                    ("pharmacy", "Pharmacy & health products"),
                    (
                        "restaurant",
                        "Restaurant & food (restaurant, café, bar, fast food)",
                    ),
                    ("general", "General retail / other"),
                ],
                default="",
                help_text="What kind of shop this branch is (gadgets, pharmacy, restaurant, ...). Left empty, it takes the business's sign-up type when saved. Changing it changes the branch's modules (branches.views.BranchViewSet.perform_update).",
                max_length=20,
            ),
        ),
        migrations.RunPython(copy_business_type, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organization",
            name="business_type",
            field=models.CharField(
                choices=[
                    ("gadgets", "Gadgets & electronics (phones, laptops, accessories)"),
                    ("clothing", "Clothing & fashion"),
                    ("pharmacy", "Pharmacy & health products"),
                    (
                        "restaurant",
                        "Restaurant & food (restaurant, café, bar, fast food)",
                    ),
                    ("general", "General retail / other"),
                ],
                default="general",
                help_text="Picked at signup: the first branch's type and the default for new branches. Each branch's own type is Shop.shop_type.",
                max_length=20,
            ),
        ),
    ]
