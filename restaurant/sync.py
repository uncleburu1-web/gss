"""Restaurant configuration in the sync pull (docs/restaurant/OFFLINE_SYNCHRONIZATION.md §4).

These entity types are OPT-IN: sync.views.SyncPullView returns them only when a client names them in
`entity_types` AND its branch has the restaurant module. A pull that names nothing (every supermarket
till and every older version) gets exactly what it got before."""
from core import modules

from .models import (DiningArea, KitchenStation, MenuCategory, MenuItem, MenuItemModifierGroup, ModifierGroup,
                     ModifierOption, RestaurantSettings, RestaurantTable, TaxRule)


def _id(value):
    return str(value) if value else None


def _settings(o):
    return {'id': str(o.id), 'hub_device_id': _id(o.hub_device_id), 'service_charge_rate': str(o.service_charge_rate),
            'service_charge_types': o.service_charge_types, 'service_charge_taxable': o.service_charge_taxable,
            'require_served_before_payment': o.require_served_before_payment, 'order_number_prefix': o.order_number_prefix,
            'business_day_starts_at': o.business_day_starts_at.strftime('%H:%M'), 'tax_state': o.tax_state,
            'vat_registered': o.vat_registered}


def _simple(*fields):
    def serialize(o):
        out = {'id': str(o.id)}
        for f in fields:
            v = getattr(o, f)
            out[f] = _id(v) if f.endswith('_id') else (str(v) if hasattr(v, 'quantize') else v)
        return out
    return serialize


def _tax(o):
    return {'id': str(o.id), 'name': o.name, 'rate': str(o.rate), 'inclusive': o.inclusive, 'tax_classes': o.tax_classes,
            'applies_to_service_charge': o.applies_to_service_charge, 'valid_from': o.valid_from.isoformat(),
            'valid_to': o.valid_to.isoformat() if o.valid_to else None, 'is_active': o.is_active}


# Parents before children, so the hub always has what a row points at.
PULL_TYPES = {
    'restaurant_settings': (RestaurantSettings, _settings),
    'dining_area': (DiningArea, _simple('name', 'sort', 'is_active')),
    'restaurant_table': (RestaurantTable, _simple('area_id', 'label', 'seats', 'sort', 'is_active')),
    'kitchen_station': (KitchenStation, _simple('name', 'prints', 'shows_on_screen', 'sort', 'is_active')),
    'menu_category': (MenuCategory, _simple('name', 'station_id', 'sort', 'is_active')),
    'modifier_group': (ModifierGroup, _simple('name', 'min_select', 'max_select', 'sort', 'is_active')),
    'modifier_option': (ModifierOption, _simple('group_id', 'name', 'price_delta', 'is_available', 'sort', 'is_active')),
    'menu_item': (MenuItem, _simple('category_id', 'name', 'description', 'price', 'station_id', 'tax_class', 'is_available', 'image_url', 'sort', 'is_active')),
    'menu_item_modifier_group': (MenuItemModifierGroup, _simple('menu_item_id', 'group_id', 'sort')),
    'tax_rule': (TaxRule, _tax),
}


def allowed_for(shop):
    """The opt-in types this branch may pull (none unless it is a restaurant branch)."""
    return PULL_TYPES if modules.has_module(shop, modules.RESTAURANT) else {}
