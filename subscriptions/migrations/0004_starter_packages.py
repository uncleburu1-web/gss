"""The starter packages (owner's decision 3). Existing subscriptions keep package = empty, which
means Retail: exactly the features they had. No prices here (still open); billing is unchanged."""
from django.db import migrations

PACKAGES = [
    ('retail', 'Retail', ['retail', 'service'], 0),
    ('restaurant', 'Restaurant', ['restaurant.tables', 'restaurant.kitchen'], 1),
    ('retail_restaurant', 'Retail + Restaurant', ['retail', 'service', 'restaurant.tables', 'restaurant.kitchen'], 2),
]


def forwards(apps, schema_editor):
    Package = apps.get_model('subscriptions', 'Package')
    for code, name, features, sort in PACKAGES:
        Package.objects.update_or_create(code=code, defaults={'name': name, 'features': features, 'sort': sort})


def backwards(apps, schema_editor):
    apps.get_model('subscriptions', 'Package').objects.filter(code__in=[p[0] for p in PACKAGES], subscriptions__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('subscriptions', '0003_package_subscription_package')]
    operations = [migrations.RunPython(forwards, backwards)]
