from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from core.models import SyncModel
from inventory.models import InventoryItem


class Sale(SyncModel):
    """A checkout — the cart header. Money (payment method, status, amount
    paid) and who/who-for (customer, staff) live here, at the whole-cart
    level, matching how a real till works: a customer pays once for
    everything in the basket. What was actually sold lives on `SaleItem`,
    one row per line in the cart.
    """
    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('transfer', 'Transfer'),
        ('pos', 'POS/Card'),
        ('mobile', 'Mobile money'),
    ]
    STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('outstanding', 'Outstanding (installment)'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    receipt_no = models.CharField(
        max_length=40, blank=True, default='', db_index=True,
        help_text='What the till printed: <till code>-<counter>, e.g. "A3F9C2-000123". Unique BY CONSTRUCTION '
                   'on each till (the code differs per till, the counter never repeats), which the plain '
                   'per-till invoice_number is not. Deliberately NOT a unique constraint here: rejecting a '
                   'sale over a numbering collision would lose a real sale for a cosmetic reason.',
    )
    invoice_number = models.PositiveIntegerField(
        null=True, blank=True, db_index=True,
        help_text='Sequential, per-shop, for display on receipts (e.g. "Invoice #1042") — NOT a global '
                   'unique key. The desktop assigns this offline, from its own local counter, so it can '
                   'print a receipt with no network round-trip; a true globally-unique number would need '
                   'exactly the round-trip the offline-first design exists to avoid. Two desktops for one '
                   'shop could in principle produce a duplicate; not enforced unique here for that reason.',
    )

    customer_name = models.CharField(max_length=150, blank=True, default='Walk-in')
    customer = models.ForeignKey(
        'customers.Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales'
    )
    staff_name = models.CharField(max_length=150, blank=True)
    worker = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales'
    )
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
    # Was auto_now_add=True, which silently stamped every OFFLINE sale with the moment it
    # finally synced instead of the moment it was rung up. default= (not auto_now_add) lets the
    # sync handler record the till's own timestamp; editable=False keeps it read-only for every
    # serializer/form, so only trusted server code (sync.views._apply_sale) can set it.
    date = models.DateTimeField(default=timezone.now, editable=False)
    device = models.ForeignKey(
        'devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales',
        help_text='The till that rang it up, when it came from a till. Null for web/Android and older tills.',
    )
    received_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the cloud received it. `date` is when it was rung up; a large gap means it was sold offline.',
    )
    shift_ref = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text="The till shift it was rung up in (shifts.Shift id). A plain id, not a foreign key: a "
                  "sale can reach the cloud before its shift does, and must never wait for it.",
    )

    class Meta:
        ordering = ['-date']

    def __str__(self):
        first_item = self.items.first()
        label = first_item.item_name if first_item else 'Sale'
        extra = self.items.count() - 1
        if extra > 0:
            label += f' +{extra} more'
        return f'{label} — {self.total}'

    # --- aggregated from line items ---------------------------------------
    @property
    def subtotal(self):
        return sum((i.subtotal for i in self.items.all()), Decimal('0.00'))

    @property
    def discount(self):
        return sum((i.discount for i in self.items.all()), Decimal('0.00'))

    @property
    def tax_amount(self):
        return sum((i.tax_amount for i in self.items.all()), Decimal('0.00'))

    @property
    def total(self):
        return sum((i.total for i in self.items.all()), Decimal('0.00'))

    @property
    def profit(self):
        return sum((i.profit for i in self.items.all()), Decimal('0.00'))

    @property
    def margin_percent(self):
        total = self.total
        if not total:
            return 0
        return round((self.profit / total) * 100, 1)

    @property
    def balance_due(self):
        if self.status == 'completed':
            return Decimal('0.00')
        return max(self.total - self.amount_paid, Decimal('0.00'))


def next_invoice_number(shop):
    """Next invoice number for `shop` — used both when a sale is created
    directly through the REST endpoint (web frontend, always online) and
    as a fallback in sync/views.py for a desktop sale that arrives with no
    invoice_number of its own (an older client, before this existed)."""
    from django.db.models import Max
    current_max = Sale.objects.filter(shop=shop).aggregate(Max('invoice_number'))['invoice_number__max']
    return (current_max or 0) + 1


class SaleItem(SyncModel):
    """One line in a sale's cart. Carries the same per-item fields the old
    single-item Sale model used to hold directly, so item-level reports
    (sales by item, best-selling, category, tax) work exactly as before —
    they just iterate SaleItem instead of Sale now.
    """
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_items'
    )
    variant = models.ForeignKey(
        'inventory.ItemVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_items',
        help_text='Which size/color was sold, when the item has variants. Left null for an item with none.',
    )
    item_name = models.CharField(max_length=200, help_text='Snapshot of the item/service name at time of sale')
    variant_label = models.CharField(max_length=100, blank=True, help_text='Snapshot of the variant (e.g. "Medium / Blue") at time of sale')
    category = models.CharField(max_length=30, blank=True, help_text='Snapshot of item category at time of sale')
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    promotion = models.ForeignKey(
        'promotions.Promotion', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_items',
        help_text='The promotion that set this line\'s discount (blank = no promotion / a manual discount).',
    )
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), help_text='Percent, e.g. 7.5')
    stock_shortfall = models.PositiveIntegerField(
        default=0,
        help_text='How many of `quantity` could NOT be matched to real stock batches at allocation time — '
                   '0 means the sale is fully backed by tracked stock. Non-zero means two tills (or a till '
                   'and the web) oversold the same item before either knew about the other\'s sale: this '
                   'many units were rung up and paid for but had no batch left to deduct from. The sale '
                   'itself is never blocked or rolled back over this — the customer already has the item, '
                   'physically, by the time this is computed — this field exists purely so the shortfall is '
                   'recorded and visible (reports, realtime alert) instead of silently absorbed into a '
                   'slightly-wrong unit_cost with no trace at all. See _allocate_stock in sales/views.py.',
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.item_name} x{self.quantity} — {self.total}'

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def tax_amount(self):
        taxable = self.subtotal - self.discount
        return round(taxable * (self.tax_rate / 100), 2)

    @property
    def total(self):
        return self.subtotal - self.discount + self.tax_amount

    @property
    def profit(self):
        return (self.unit_price - self.unit_cost) * self.quantity - self.discount

    @property
    def margin_percent(self):
        if not self.total:
            return 0
        return round((self.profit / self.total) * 100, 1)


class SaleAllocation(SyncModel):
    """Tracks exactly which stock batch(es) a sale line item drew units
    from, so deleting/replacing a line can restore stock precisely instead
    of guessing."""
    sale_item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name='allocations')
    batch = models.ForeignKey('inventory.StockBatch', on_delete=models.SET_NULL, null=True, related_name='allocations')
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f'{self.sale_item_id} -> batch {self.batch_id}: {self.quantity}'


class SalePayment(SyncModel):
    """One payment taken against a sale: one row per method used, so a sale can be paid by
    cash + transfer + card, and an installment sale collects several payments over time.

    Append-only. Sale.amount_paid is DERIVED from these rows (sales.payments.recompute_paid),
    never written from a client's absolute number: two payments taken on two devices while
    offline both count instead of one overwriting the other (G2 in the architecture analysis).
    `amount` is what was applied to the sale; for cash, `tendered` is what the customer handed
    over and `change_given` what went back. A negative amount is a refund (e.g. an item swapped
    for a cheaper one)."""
    METHOD_CHOICES = Sale.PAYMENT_CHOICES + [('other', 'Other')]
    SOURCE_CHOICES = [
        ('till', 'Desktop till'),
        ('web', 'Web / Android'),
        ('legacy', 'Recorded before payments were itemised'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_given = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    reference = models.CharField(max_length=100, blank=True, default='')
    paid_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='till',
        help_text='"legacy" = one lump payment standing in for an amount recorded before payments were '
                  'itemised (backfill, or an older till). A till that already has its own itemised '
                  'payments for the sale must not add a legacy row on top (it would count twice).',
    )
    shift_ref = models.UUIDField(
        null=True, blank=True,
        help_text="The till's shift id, kept as a plain reference until shifts are uploaded (stage 3).",
    )
    device = models.ForeignKey(
        'devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_payments',
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['paid_at', 'created_at']

    def __str__(self):
        return f'{self.method} {self.amount} on sale {self.sale_id}'


class SaleReturn(SyncModel):
    """Items a customer brought back on a completed sale, and the money refunded.

    A return is its OWN record: the original sale (its lines, payments and receipt) is never
    rewritten. Reports subtract returns in the period they happen; the till's shift and day reports
    count refunds by method, and a cash refund comes out of the drawer's expected cash.
    Append-only. `id` is the till's id when it was recorded on a till, so a re-send is recognised.
    """
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='returns')
    returned_at = models.DateTimeField(default=timezone.now, db_index=True)
    reason = models.CharField(max_length=255)
    refund_method = models.CharField(max_length=20, choices=SalePayment.METHOD_CHOICES, default='cash')
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='What the returned items were sold for (after their share of any discount).',
    )
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    actor_name = models.CharField(max_length=150, blank=True)
    source = models.CharField(max_length=10, default='web')
    device = models.ForeignKey('devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    shift_ref = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ['-returned_at']

    def __str__(self):
        return f'Return of {self.total_value} on sale {self.sale_id}'


class SaleReturnLine(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name='lines')
    sale_item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name='return_lines')
    quantity = models.PositiveIntegerField()
    value = models.DecimalField(max_digits=12, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    restocked = models.BooleanField(default=True, help_text='False = damaged/unsellable: written off, not put back.')

    def __str__(self):
        return f'{self.quantity} x {self.sale_item.item_name}'
