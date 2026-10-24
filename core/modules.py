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

# The modules each shop type runs (owner's decision 13: a restaurant is a shop type, picked like
# gadgets or pharmacy). A restaurant keeps `retail` too: its other tills still sell stocked goods
# (drinks, takeaway packs) as an ordinary till, and the hub till runs the restaurant.
TYPE_MODULES = {
    'gadgets': [RETAIL, SERVICE],
    'restaurant': [RETAIL, RESTAURANT],
}


# The worker roles each kind of shop offers when adding staff (owner's request, 23 Oct 2026: a
# restaurant hires waiters, cashiers and kitchen staff, not sellers and technicians). The owner and
# the branch manager come from sign-up and Settings -> Branches, not from this list.
RESTAURANT_ROLES = ['waiter', 'cashier', 'kitchen']
SHOP_ROLES = ['seller', 'reception', 'technician', 'attendant']
TYPE_ROLES = {'restaurant': RESTAURANT_ROLES + ['other']}


def type_roles(shop_type):
    return TYPE_ROLES.get(shop_type, SHOP_ROLES + ['other'])


def type_modules(shop_type):
    return TYPE_MODULES.get(shop_type, [RETAIL])


def missing_features_for_type(organization, shop_type):
    """The modules a shop type needs that the business's package does not include (empty = allowed)."""
    return [m for m in type_modules(shop_type) if not has_feature(organization, MODULE_FEATURE[m])]


def apply_type_modules(shop, user=None):
    """Make the branch's modules exactly its type's: switch the type's modules on and the rest off.
    Switching off hides a module; nothing it recorded is deleted. Returns {module: on} for what changed."""
    wanted = set(type_modules(shop.shop_type))
    current = branch_modules(shop)
    changes = {m: (m in wanted) for m in MODULES if (m in wanted) != (m in current)}
    for module, on in changes.items():
        row, _ = BranchModule.objects.get_or_create(shop=shop, module=module, defaults={'enabled': on, 'changed_by': user})
        row.enabled, row.changed_by = on, user
        row.save(update_fields=['enabled', 'changed_by', 'changed_at'])
    return changes


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
