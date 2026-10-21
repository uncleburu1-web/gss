"""Branch modules and package features: the one place that answers "may this branch use X?".

  * A branch's modules are BranchModule rows (retail / restaurant / service).
  * Which modules a business may switch on comes from its subscription package's features
    (subscriptions.models.Package). During the free trial every feature is available.
  * Billing never stops a module that is already on: an expired subscription pauses cloud services
    exactly as for supermarket tills (subscriptions/gate.py), and a package change only stops NEW
    switch-ons and NEW restaurant set-up. A restaurant mid-service is never cut off.
"""
from .models import BranchModule

RETAIL, RESTAURANT, SERVICE = BranchModule.RETAIL, BranchModule.RESTAURANT, BranchModule.SERVICE
MODULES = [m for m, _ in BranchModule.MODULE_CHOICES]

# The package feature a module needs before it can be switched on.
MODULE_FEATURE = {RETAIL: 'retail', SERVICE: 'service', RESTAURANT: 'restaurant.tables'}


def branch_modules(shop):
    """The set of modules switched on for a branch."""
    return set(BranchModule.objects.filter(shop=shop, enabled=True).values_list('module', flat=True))


def has_module(shop, module):
    return shop is not None and BranchModule.objects.filter(shop=shop, module=module, enabled=True).exists()


def organization_features(organization):
    from subscriptions.models import get_or_create_subscription
    return get_or_create_subscription(organization).features


def has_feature(organization, feature):
    return organization is not None and feature in organization_features(organization)


def can_enable(shop, module):
    """True if the business's package lets this branch switch `module` on."""
    return has_feature(shop.organization, MODULE_FEATURE[module])
