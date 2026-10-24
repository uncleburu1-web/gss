"""Give every existing branch the module it has always had: retail, plus service where the business
has repairs today (business_type 'gadgets' — Organization.REPAIR_CAPABLE_TYPES). Adds rows only;
nothing is removed. Reversing removes only the rows this migration added."""
from django.db import migrations

REPAIR_CAPABLE_TYPES = {'gadgets'}


def forwards(apps, schema_editor):
    Shop = apps.get_model('core', 'Shop')
    BranchModule = apps.get_model('core', 'BranchModule')
    for shop in Shop.objects.select_related('organization'):
        BranchModule.objects.get_or_create(shop=shop, module='retail', defaults={'enabled': True})
        if shop.organization_id and shop.organization.business_type in REPAIR_CAPABLE_TYPES:
            BranchModule.objects.get_or_create(shop=shop, module='service', defaults={'enabled': True})


def backwards(apps, schema_editor):
    apps.get_model('core', 'BranchModule').objects.filter(changed_by__isnull=True, module__in=['retail', 'service']).delete()


class Migration(migrations.Migration):
    dependencies = [('core', '0010_branchmodule')]
    operations = [migrations.RunPython(forwards, backwards)]
