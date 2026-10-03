from django.db import models
<<<<<<< HEAD
=======
from django.utils import timezone
>>>>>>> 6f155c9 (Add expense support)
from core.models import SyncModel


class Liability(SyncModel):
<<<<<<< HEAD
=======
    """Money the business currently owes — a staff salary not yet paid, a
    supplier balance, a rent bill, a loan, ... `amount` is the ORIGINAL
    obligation and is never mutated after creation; how much has actually
    been paid off lives entirely in this liability's `payments`
    (LiabilityPayment, below), never as a single balance field edited in
    place. That's deliberate: the old version of this model just flipped
    a pending/paid flag on `status` with no record of individual
    payments, so clearing a liability destroyed the history of how it got
    there — see the 0002 migration, which reconstructs one payment per
    legacy "paid" row so that history isn't lost for liabilities that
    already existed before this changed.

    amount_paid/outstanding are computed from `payments`, never stored —
    the one source of truth is the payments themselves, so these two
    numbers can't ever drift out of sync with reality the way a
    hand-edited balance field could.
    """
>>>>>>> 6f155c9 (Add expense support)
    CATEGORY_CHOICES = [
        ('rent', 'Shop rent'),
        ('loan', 'Loan'),
        ('utility', 'Utility bill'),
        ('salary', 'Staff salary owed'),
        ('supplier_credit', 'Supplier credit'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
<<<<<<< HEAD
        ('pending', 'Pending'),
        ('paid', 'Paid'),
=======
        ('unpaid', 'Unpaid'),
        ('partially_paid', 'Partially paid'),
        ('cleared', 'Cleared'),
>>>>>>> 6f155c9 (Add expense support)
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
<<<<<<< HEAD
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['due_date', '-created_at']

    def __str__(self):
        return f'{self.name} — {self.amount}'
=======
    owed_to = models.CharField(max_length=200, blank=True, help_text='Who or which company this is owed to.')
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text='The original amount owed — never edited after creation; see class docstring.')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default='unpaid',
        help_text='Maintained automatically from `payments` by refresh_status() — not meant to be set '
                   'directly by a client; see LiabilityPaymentViewSet.',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='liabilities_created',
    )
    cleared_at = models.DateTimeField(null=True, blank=True, help_text='When status last became "cleared".')

    class Meta:
        ordering = ['due_date', '-created_at']
        indexes = [
            models.Index(fields=['shop', 'status']),
        ]

    def __str__(self):
        return f'{self.name} — {self.amount}'

    # --- derived from payments, never stored directly ----------------------
    @property
    def amount_paid(self):
        total = self.payments.filter(is_deleted=False).aggregate(total=models.Sum('amount'))['total']
        return total or 0

    @property
    def outstanding(self):
        return max(self.amount - self.amount_paid, 0)

    def refresh_status(self, save=True):
        """Recompute `status`/`cleared_at` from `payments`. Called after
        every payment create/update/delete (see LiabilityPaymentViewSet) —
        never left for a client to set `status` directly, so it can't
        drift out of sync with the actual payment total."""
        paid = self.amount_paid
        if paid <= 0:
            new_status = 'unpaid'
        elif paid < self.amount:
            new_status = 'partially_paid'
        else:
            new_status = 'cleared'

        self.status = new_status
        if new_status == 'cleared':
            if not self.cleared_at:
                self.cleared_at = timezone.now()
        else:
            self.cleared_at = None

        if save:
            self.save(update_fields=['status', 'cleared_at', 'updated_at'])


class LiabilityPayment(SyncModel):
    """One payment made toward a Liability's outstanding balance — the
    PAYMENT concept, kept deliberately separate from both the Liability
    (the obligation) and Expense (a direct business cost — see
    expenses.models.Expense's docstring). Every liability payment gets its
    own row here, in full, forever: clearing a liability never collapses
    or deletes this history.

    Whether a payment counts as an operating expense for reporting
    (Executive Overview, Expense Analytics) depends only on the parent
    liability's category — see counts_as_expense below and
    reports/analytics.py's EXPENSE_LIABILITY_CATEGORIES, which is the
    single place that classification lives. This model never creates or
    links to an Expense row: the payment IS the expense event for that
    money, in the period it was actually paid (cash-basis), so a separate
    Expense entry for the same payment would double-count it.
    """
    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('transfer', 'Transfer'),
        ('pos', 'POS/Card'),
    ]
    # Categories whose payments represent a genuine operating cost. 'loan'
    # is excluded — repaying debt principal is a financing cash outflow,
    # not an operating expense. 'supplier_credit' is excluded too — it
    # settles a payable for inventory that's already costed through COGS
    # when it sells (see sales.models.SaleItem.profit); counting the
    # payment as a fresh expense as well would double-count against COGS.
    EXPENSE_CATEGORIES = {'salary', 'rent', 'utility', 'other'}

    liability = models.ForeignKey(Liability, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='liability_payments_recorded',
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-paid_at']
        indexes = [
            models.Index(fields=['shop', 'paid_at']),
        ]

    def __str__(self):
        return f'{self.liability.name}: {self.amount} on {self.paid_at:%Y-%m-%d}'

    @property
    def counts_as_expense(self):
        return self.liability.category in self.EXPENSE_CATEGORIES
>>>>>>> 6f155c9 (Add expense support)
