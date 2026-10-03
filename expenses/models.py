from django.db import models
from django.utils import timezone
from core.models import SyncModel

# Seeded into a shop's category list the first time it's fetched and empty
# (see ExpenseCategoryViewSet.get_queryset) — NOT a fixed/hardcoded choices
# list on the model itself. Every one of these is a completely ordinary,
# editable ExpenseCategory row from the moment it's created: an owner can
# rename, deactivate, or delete any of them, and add their own. This list
# only decides what a brand-new shop starts with.
DEFAULT_EXPENSE_CATEGORIES = [
    'Fuel', 'Electricity', 'Water', 'Rent', 'Salary', 'Transport', 'Maintenance',
    'Repairs', 'Internet/Data', 'Packaging', 'Cleaning', 'Marketing', 'Tax/Levy', 'Other',
]


class ExpenseCategory(SyncModel):
    """A shop-managed expense category (Fuel, Rent, ...) — deliberately a
    real editable table, not a hardcoded CharField `choices` list, per the
    requirement that categories be "manageable by authorized
    administrators instead of being permanently hard-coded." Gated by the
    same 'manage_expenses' capability as Expense itself (see
    core.capabilities) — a category is part of expense bookkeeping, not a
    separate permission surface.

    "Deactivating" a category is just the normal DELETE this whole
    codebase already uses everywhere (ShopScopedMixin.perform_destroy —
    is_deleted=True, hidden from the picker) — no separate is_active flag:
    that would just be a second boolean meaning almost the same thing.
    Expense.category is PROTECT, not CASCADE, so a category already used
    by an expense can still be soft-deleted (hidden going forward) while
    every existing Expense keeps reading its real category name straight
    off the FK, same as a soft-deleted Worker's name still shows correctly
    on old Sales.
    """
    name = models.CharField(max_length=60)
    is_default = models.BooleanField(
        default=False, help_text='True for one of the seeded starter categories — informational only, '
                                  'never affects behavior; a default category can be renamed or deleted '
                                  'exactly like a custom one.',
    )

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['shop', 'name'], name='unique_expense_category_name_per_shop'),
        ]

    def __str__(self):
        return self.name


class Expense(SyncModel):
    """A single business cost — money that actually left the business
    right now (fuel, rent, a repair bill, ...). Deliberately NOT how a
    Liability being paid off is recognized as a cost — see
    LiabilityPayment.counts_as_expense on the liabilities app and
    reports/analytics.py's EXPENSE_LIABILITY_CATEGORIES for why that's a
    separate path: a Liability's payment already IS the expense event for
    that money, so it must never also get a duplicate Expense row, and an
    Expense here must never be created FOR settling a liability — see
    LiabilityPaymentSerializer, which is the one and only way a liability
    payment is recorded.
    """
    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('transfer', 'Transfer'),
        ('pos', 'POS/Card'),
    ]

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name='expenses',
        help_text='PROTECT, not SET_NULL/CASCADE: a category in use cannot be hard-deleted out from under '
                   'historical expenses — see ExpenseCategoryViewSet.perform_destroy, which deactivates '
                   'instead.',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
    # The expense's OWN date/time (when the money was actually spent), not
    # necessarily right now — a shop owner often logs yesterday's fuel
    # receipt today. created_at (from SyncModel) is separately preserved
    # as "when this record was entered," for the audit trail.
    date = models.DateField(default=timezone.localdate)
    time = models.TimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_recorded',
    )
    receipt_url = models.URLField(
        max_length=500, null=True, blank=True,
        help_text='Cloudinary-hosted photo of the receipt/document — optional. Same pattern as '
                   'InventoryItem.image_url; only ever set through the dedicated `receipt` upload action.',
    )
    receipt_public_id = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['shop', 'date']),
        ]

    def __str__(self):
        return f'{self.category.name} — {self.amount} ({self.date})'
