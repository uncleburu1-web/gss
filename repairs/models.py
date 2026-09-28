from decimal import Decimal
from django.db import models
from django.utils import timezone
from core.models import SyncModel


class RepairTicket(SyncModel):
    STATUS_CHOICES = [
        ('received', 'Received'),
        ('diagnosing', 'Diagnosing'),
        ('in_repair', 'In repair'),
        ('ready', 'Ready for pickup'),
        ('collected', 'Collected'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('installment', 'Installment (part payment)'),
        ('paid', 'Paid in full'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('urgent', 'Urgent'),
    ]

    ticket_no = models.CharField(max_length=20, editable=False)
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=30, blank=True)
    device = models.CharField(max_length=200)
    issue = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default='normal',
        help_text='Lets a busy bench sort the queue instead of working strictly first-in-first-out.',
    )
    technician = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tickets',
        help_text='Who is actually working the bench job. Not required — a one-tech shop can leave this unset.',
    )
    estimated_ready = models.DateTimeField(
        null=True, blank=True, help_text='What you told the customer to expect — shown on the ticket/receipt.',
    )
    warranty_days = models.PositiveIntegerField(
        default=0, help_text='Days of warranty on the repair itself, counted from date_out. 0 = none offered.',
    )
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='installment')
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    date_in = models.DateTimeField(auto_now_add=True)
    date_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date_in']
        constraints = [
            models.UniqueConstraint(fields=['shop', 'ticket_no'], name='unique_ticket_no_per_shop'),
        ]

    def __str__(self):
        return f'{self.ticket_no} — {self.device}'

    @property
    def balance_due(self):
        return max(Decimal('0'), self.cost - self.amount_paid)

    @property
    def is_paid(self):
        return self.payment_status == 'paid'

    @property
    def parts_cost(self):
        return sum((p.quantity * p.unit_cost for p in self.parts_used.filter(is_deleted=False)), Decimal('0'))

    @property
    def warranty_expires(self):
        if not self.warranty_days or not self.date_out:
            return None
        return self.date_out + timezone.timedelta(days=self.warranty_days)

    def apply_payment(self, amount):
        """Record money actually received against this ticket. Bumps
        amount_paid, logs a dated ledger entry (so 'collected today' figures
        are accurate regardless of when the ticket was opened), and flips
        payment_status to 'paid' once the balance clears."""
        amount = Decimal(str(amount))
        if amount <= 0:
            return
        self.amount_paid += amount
        if self.cost and self.amount_paid >= self.cost:
            self.amount_paid = self.cost
            self.payment_status = 'paid'
        self.save()
        RepairPayment.objects.create(ticket=self, amount=amount, shop=self.shop)

    def save(self, *args, **kwargs):
        if not self.ticket_no:
            # UUID PKs aren't sequential, so the ticket number is generated
            # from a per-shop count instead of the old `last.id + 1` trick.
            # Tiny race window under concurrent creates on the same shop is
            # acceptable here (matches the previous code's own guarantees);
            # revisit with a DB sequence per shop if that ever matters.
            count = RepairTicket.objects.filter(shop=self.shop).count()
            self.ticket_no = f'RPR-{count + 1:04d}'
        if self.status == 'collected' and not self.date_out:
            self.date_out = timezone.now()
        super().save(*args, **kwargs)


class RepairPayment(SyncModel):
    """A dated ledger entry for money received on a repair ticket — lets
    dashboard/report 'collected today' figures reflect the day money was
    actually received, not the day the ticket was opened."""
    ticket = models.ForeignKey(RepairTicket, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f'{self.ticket.ticket_no} — {self.amount}'


class RepairPart(SyncModel):
    """One inventory item consumed on a service ticket (a replacement
    screen, a battery, a cable...). Deducting real stock for a part used
    on a job — instead of just typing "screen repair" as a free-text line
    like the old flow — is what turns this into an actual parts-tracked
    bench, not just a job/payment tracker: cost of goods on a repair now
    shows up in inventory valuation and profit reporting the same way a
    sold product does. Stock is allocated FEFO across StockBatch rows the
    same way sales/views.py._allocate_stock works for a Sale line; see
    repairs/views.py for that logic.
    """
    ticket = models.ForeignKey(RepairTicket, on_delete=models.CASCADE, related_name='parts_used')
    item = models.ForeignKey('inventory.InventoryItem', on_delete=models.PROTECT, related_name='used_in_repairs')
    quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Weighted-average batch cost at the moment the part was taken — frozen here so it never '
                   'silently shifts if new stock arrives at a different cost later.',
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-added_at']

    def __str__(self):
        return f'{self.ticket.ticket_no} — {self.quantity}× {self.item.name}'

