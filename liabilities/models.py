from django.db import models
from core.models import SyncModel


class Liability(SyncModel):
    CATEGORY_CHOICES = [
        ('rent', 'Shop rent'),
        ('loan', 'Loan'),
        ('utility', 'Utility bill'),
        ('salary', 'Staff salary owed'),
        ('supplier_credit', 'Supplier credit'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['due_date', '-created_at']

    def __str__(self):
        return f'{self.name} — {self.amount}'
