"""Restaurant configuration: edited in the cloud by owners, pulled by the branch's hub till.

Design: docs/restaurant/DOMAIN_MODEL.md §2. Every model is a SyncModel, so every row belongs to one
branch (`shop`) and every query is filtered by the branches the user may see. Names are unique per
branch among rows that are not deleted, so a deactivated "Grill" can be recreated.

Orders, tickets, bills and payments (DOMAIN_MODEL §3) live on the hub and arrive later as
documents; they are not in this module yet.
"""
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.models import SyncModel

ZERO = Decimal('0')
HUNDRED = Decimal('100')
SERVICE_TYPES = ['dine_in', 'takeaway', 'counter']


def _unique_name(model, field='name'):
    return models.UniqueConstraint(fields=['shop', field], condition=Q(is_deleted=False),
                                   name=f'unique_{model}_{field}_per_branch')


class RestaurantSettings(SyncModel):
    """One per branch: service charge, the served-before-payment rule, numbering, business day, the
    hub, and the tax activation record (REQUIREMENTS §4 safeguards)."""
    hub_device = models.ForeignKey('devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                   help_text='The ONE till allowed to run this restaurant (one writer per branch).')
    service_charge_rate = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO,
                                              validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)])
    service_charge_types = models.JSONField(default=list, blank=True, help_text='e.g. ["dine_in"]')
    service_charge_taxable = models.BooleanField(default=False)
    require_served_before_payment = models.BooleanField(default=False)
    order_number_prefix = models.CharField(max_length=5, blank=True, default='')
    business_day_starts_at = models.TimeField(default=datetime.time(5, 0))
    tax_state = models.CharField(max_length=60, blank=True, default='', help_text='Nigerian state the branch is in')
    vat_registered = models.BooleanField(null=True, blank=True)
    tax_treatment_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    tax_treatment_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['shop'], condition=Q(is_deleted=False), name='one_restaurant_settings_per_branch')]


class DiningArea(SyncModel):
    name = models.CharField(max_length=60)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'name']
        constraints = [_unique_name('diningarea')]

    def __str__(self):
        return self.name


class RestaurantTable(SyncModel):
    """A table. Its occupancy is NOT stored: it is derived from open orders on the hub."""
    area = models.ForeignKey(DiningArea, on_delete=models.PROTECT, related_name='tables')
    label = models.CharField(max_length=20)
    seats = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1)])
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'label']
        constraints = [_unique_name('restauranttable', 'label')]

    def __str__(self):
        return self.label


class KitchenStation(SyncModel):
    """Where food is made. WHICH printer it uses is set on the hub PC, never here: a Windows printer
    name only means something on the PC it is installed on (owner's decision 5: receipts and kitchen
    tickets are configured separately)."""
    name = models.CharField(max_length=60)
    prints = models.BooleanField(default=True)
    shows_on_screen = models.BooleanField(default=False)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'name']
        constraints = [
            _unique_name('kitchenstation'),
            models.CheckConstraint(condition=Q(prints=True) | Q(shows_on_screen=True), name='station_prints_or_shows'),
        ]

    def __str__(self):
        return self.name


class MenuCategory(SyncModel):
    name = models.CharField(max_length=80)
    station = models.ForeignKey(KitchenStation, on_delete=models.SET_NULL, null=True, blank=True, related_name='categories',
                                help_text='Where items in this category are made, unless an item says otherwise.')
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'name']
        constraints = [_unique_name('menucategory')]

    def __str__(self):
        return self.name


class MenuItem(SyncModel):
    category = models.ForeignKey(MenuCategory, on_delete=models.PROTECT, related_name='items')
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default='')
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)])
    station = models.ForeignKey(KitchenStation, on_delete=models.SET_NULL, null=True, blank=True, related_name='items',
                                help_text="Overrides the category's station.")
    tax_class = models.CharField(max_length=20, default='food', help_text='Which tax rules apply, e.g. food or drink')
    is_available = models.BooleanField(default=True, help_text='Sold-out switch (also flipped on the hub during service)')
    image_url = models.URLField(blank=True, default='')
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    modifier_groups = models.ManyToManyField('ModifierGroup', through='MenuItemModifierGroup', related_name='menu_items')

    class Meta:
        ordering = ['sort', 'name']
        constraints = [_unique_name('menuitem')]

    def __str__(self):
        return self.name

    @property
    def effective_station(self):
        return self.station or self.category.station


class ModifierGroup(SyncModel):
    """"Size", "Extras". A required single choice is min_select = max_select = 1 (this replaces
    separate variants)."""
    name = models.CharField(max_length=60)
    min_select = models.PositiveSmallIntegerField(default=0)
    max_select = models.PositiveSmallIntegerField(default=1)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'name']
        constraints = [
            _unique_name('modifiergroup'),
            models.CheckConstraint(condition=Q(min_select__lte=models.F('max_select')), name='modifier_min_le_max'),
        ]

    def __str__(self):
        return self.name


class ModifierOption(SyncModel):
    group = models.ForeignKey(ModifierGroup, on_delete=models.PROTECT, related_name='options')
    name = models.CharField(max_length=60)
    price_delta = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    is_available = models.BooleanField(default=True)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'name']
        constraints = [models.UniqueConstraint(fields=['group', 'name'], condition=Q(is_deleted=False), name='unique_option_per_group')]

    def __str__(self):
        return self.name


class MenuItemModifierGroup(SyncModel):
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='modifier_links')
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name='item_links')
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort']
        constraints = [models.UniqueConstraint(fields=['menu_item', 'group'], condition=Q(is_deleted=False), name='unique_item_modifier_group')]


class TaxRule(SyncModel):
    """A tax charged to customers (VAT, a state consumption tax, …). Created INACTIVE; activated only
    through the activation endpoint with the safeguards of REQUIREMENTS §4. Once active, the parts
    that decide amounts cannot be edited: deactivate it and create a new rule with a start date, so
    the record of what applied when stays true. Bills keep their own frozen copy anyway."""
    name = models.CharField(max_length=60)
    rate = models.DecimalField(max_digits=7, decimal_places=4, validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)])
    inclusive = models.BooleanField(default=False, help_text='Menu prices already include this tax')
    tax_classes = models.JSONField(default=list, blank=True, help_text='Tax classes it covers; empty = every item')
    applies_to_service_charge = models.BooleanField(default=False)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    activated_at = models.DateTimeField(null=True, blank=True)
    overlap_confirmed = models.BooleanField(default=False)

    AMOUNT_FIELDS = ('rate', 'inclusive', 'tax_classes', 'applies_to_service_charge', 'valid_from')

    class Meta:
        ordering = ['valid_from', 'name']
        indexes = [models.Index(fields=['shop', 'valid_from'])]

    def __str__(self):
        return f'{self.name} {self.rate}%'

    def overlaps(self, other):
        """Would both rules apply to the same item on some day?"""
        if self.pk == other.pk:
            return False
        far = datetime.date.max
        if self.valid_from > (other.valid_to or far) or other.valid_from > (self.valid_to or far):
            return False
        mine, theirs = set(self.tax_classes or []), set(other.tax_classes or [])
        return not mine or not theirs or bool(mine & theirs)
