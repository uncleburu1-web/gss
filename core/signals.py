"""Every new branch starts as a supermarket branch (`retail`, plus `service` where the business has
repairs), exactly what a branch was before modules existed. Covers both creation paths (sign-up in
core.views.RegisterView and BranchSerializer.create) without touching them."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BranchModule, Shop


def default_modules_for(shop):
    modules = [BranchModule.RETAIL]
    if shop.organization_id and shop.organization.repairs_enabled:
        modules.append(BranchModule.SERVICE)
    return modules


@receiver(post_save, sender=Shop)
def give_new_branch_default_modules(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    for module in default_modules_for(instance):
        BranchModule.objects.get_or_create(shop=instance, module=module)
