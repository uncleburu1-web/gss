from django.db import models
from django.utils import timezone
from core.models import SyncModel


class Liability(SyncModel):
    """Money the business currently owes — a staff salary not yet paid, a
    supplier balance, a rent bill, a loan, ... `amount` is the ORIGINAL
    obligation and is never mutated after creation; how much has actually
    been paid off lives entirely in this liability's `payments`
    (LiabilityPayment, below), never as a single balance field edited in
    place.
    """

    CATEGORY_CHOICES = [
        ('rent', 'Shop rent'),
        ('loan', 'Loan'),
        ('utility', 'Utility bill'),
        ('salary', 'Staff salary owed'),
        ('supplier_credit', 'Supplier credit'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('partially_paid', 'Partially paid'),
        ('cleared', 'Cleared'),
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='other'
    )
    owed_to = models.CharField(
        max_length=200,
        blank=True,
        help_text='Who or which company this is owed to.'
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text='The original amount owed — never edited after creation.'
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default='unpaid',
        help_text='Maintained automatically from payments.'
    )
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        'staff.Worker',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='liabilities_created',
    )

    cleared_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When status last became "cleared".'
    )

    class Meta:
        ordering = ['due_date', '-created_at']
        indexes = [
            models.Index(fields=['shop', 'status']),
        ]

    def __str__(self):
        return f'{self.name} — {self.amount}'

    @property
    def amount_paid(self):
        total = self.payments.filter(
            is_deleted=False
        ).aggregate(
            total=models.Sum('amount')
        )['total']

        return total or 0

    @property
    def outstanding(self):
        return max(self.amount - self.amount_paid, 0)

    def refresh_status(self, save=True):
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
            self.save(
                update_fields=[
                    'status',
                    'cleared_at',
                    'updated_at'
                ]
            )


class LiabilityPayment(SyncModel):
    """One payment made toward a Liability's outstanding balance."""

    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('transfer', 'Transfer'),
        ('pos', 'POS/Card'),
    ]

    EXPENSE_CATEGORIES = {
        'salary',
        'rent',
        'utility',
        'other'
    }

    liability = models.ForeignKey(
        Liability,
        on_delete=models.CASCADE,
        related_name='payments'
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES,
        default='cash'
    )

    paid_at = models.DateTimeField(
        default=timezone.now
    )

    recorded_by = models.ForeignKey(
        'staff.Worker',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='liability_payments_recorded',
    )

    notes = models.CharField(
        max_length=200,
        blank=True
    )

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
