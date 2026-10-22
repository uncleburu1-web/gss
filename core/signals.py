"""Every new branch starts with its shop type's modules (core.modules.TYPE_MODULES): `retail`, plus
`service` for a gadget shop (what a branch always had) and `restaurant` for a restaurant. Covers every
creation path (sign-up in core.views.RegisterView, BranchViewSet, the admin) without touching them."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BranchModule, Shop


def default_modules_for(shop):
    from .modules import type_modules
    return type_modules(shop.shop_type)


@receiver(post_save, sender=Shop)
def give_new_branch_default_modules(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    for module in default_modules_for(instance):
        BranchModule.objects.get_or_create(shop=instance, module=module)
