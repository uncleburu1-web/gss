from decimal import Decimal
from django.db import models
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
    ]
    STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('outstanding', 'Outstanding (installment)'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
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
    date = models.DateTimeField(auto_now_add=True)

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
