"""Restaurant configuration: edited in the cloud by owners, pulled by the branch's hub till.

Design: docs/restaurant/DOMAIN_MODEL.md §2. Every model is a SyncModel, so every row belongs to one
branch (`shop`) and every query is filtered by the branches the user may see. Names are unique per
branch among rows that are not deleted, so a deactivated "Grill" can be recreated.

Orders, tickets, bills and payments (DOMAIN_MODEL §3) live on the hub while open. A finished order
arrives once, as one `order_doc` (restaurant/ingest.py), and is stored by the models at the end of
this module with the hub's own ids. The cloud never changes them afterwards.
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


# ---------------------------------------------------------------------------------------------
# Finished orders, as the hub sent them (DOMAIN_MODEL §3). Written only by restaurant/ingest.py,
# in one transaction per order; read by reports. Prices, names and rates are the hub's frozen copies.
# ---------------------------------------------------------------------------------------------
MONEY = dict(max_digits=12, decimal_places=2)
QTY = dict(max_digits=12, decimal_places=3)


class Order(SyncModel):
    STATUS_CHOICES = [('closed', 'Closed'), ('cancelled', 'Cancelled')]
    number = models.CharField(max_length=30)
    service_type = models.CharField(max_length=10, choices=[(t, t) for t in SERVICE_TYPES])
    table = models.ForeignKey(RestaurantTable, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    guests = models.PositiveSmallIntegerField(null=True, blank=True)
    waiter = models.CharField(max_length=150, blank=True)
    opened_by = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    business_date = models.DateField()
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    pricing = models.JSONField(default=dict, help_text='Service charge and tax rules frozen when the order opened')
    device = models.ForeignKey('devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    sale = models.OneToOneField('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True, related_name='restaurant_order',
                                help_text='The Sale that puts this order in the existing reports (ADR-023); none when cancelled')
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-closed_at']
        indexes = [models.Index(fields=['shop', 'status']), models.Index(fields=['shop', 'business_date'])]

    def __str__(self):
        return f'{self.number} ({self.business_date})'


class OrderItem(SyncModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    name = models.CharField(max_length=120)
    unit_price = models.DecimalField(**MONEY)
    modifiers_price = models.DecimalField(**MONEY, default=ZERO)
    tax_class = models.CharField(max_length=30, blank=True)
    quantity = models.DecimalField(**QTY)
    voided_quantity = models.DecimalField(**QTY, default=ZERO)
    state = models.CharField(max_length=12)
    waste = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']


class OrderItemModifier(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='modifiers')
    option = models.ForeignKey(ModifierOption, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    name = models.CharField(max_length=120)
    price_delta = models.DecimalField(**MONEY, default=ZERO)


class KitchenTicket(SyncModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='tickets')
    station = models.ForeignKey(KitchenStation, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    number = models.CharField(max_length=30)
    kind = models.CharField(max_length=8)  # new | void
    sent_at = models.DateTimeField()
    sent_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['sent_at']
        indexes = [models.Index(fields=['shop', 'station', 'sent_at'])]


class KitchenTicketItem(SyncModel):
    ticket = models.ForeignKey(KitchenTicket, on_delete=models.CASCADE, related_name='items')
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='ticket_items')
    quantity = models.DecimalField(**QTY)
    voided_quantity = models.DecimalField(**QTY, default=ZERO)
    state = models.CharField(max_length=12)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)


class Bill(SyncModel):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='bill')
    number = models.CharField(max_length=40)
    status = models.CharField(max_length=10)
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY, default=ZERO)
    discount_reason = models.CharField(max_length=255, blank=True)
    net = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)


class BillCharge(models.Model):
    """A service-charge or tax line, frozen as printed: the record of what was charged at what rate."""
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='charges')
    kind = models.CharField(max_length=20)  # service_charge | tax
    tax_rule = models.ForeignKey(TaxRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    name = models.CharField(max_length=60)
    rate = models.DecimalField(max_digits=7, decimal_places=4)
    inclusive = models.BooleanField(default=False)
    base = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY)
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort']


class BillPayment(SyncModel):
    """Money received on the hub. Tips are outside the bill (REQUIREMENTS §4)."""
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=20)
    amount = models.DecimalField(**MONEY)
    tendered = models.DecimalField(**MONEY, null=True, blank=True)
    change = models.DecimalField(**MONEY, default=ZERO)
    tip = models.DecimalField(**MONEY, default=ZERO)
    reference = models.CharField(max_length=100, blank=True)
    shift_ref = models.UUIDField(null=True, blank=True, db_index=True)
    received_by = models.CharField(max_length=150, blank=True)
    received_at = models.DateTimeField()

    class Meta:
        ordering = ['received_at']


class MenuAvailability(models.Model):
    """The hub's sold-out switch for one business day. Recorded, never copied onto MenuItem: the
    hub's flag rules for that day, and tomorrow the item is back (OFFLINE_SYNCHRONIZATION §4)."""
    shop = models.ForeignKey('core.Shop', on_delete=models.CASCADE, related_name='+')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='availability')
    business_date = models.DateField()
    sold_out = models.BooleanField()
    by = models.CharField(max_length=150, blank=True)
    at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['menu_item', 'business_date'], name='one_availability_per_item_per_day')]
