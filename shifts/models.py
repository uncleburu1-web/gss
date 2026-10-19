"""Cashier shifts and their cash movements, uploaded from the tills (stage 3).

A shift is the till's own record: opened with a float, sales and payments taken during it, cash
paid in or out with a reason, then closed with a blind count. At close the till freezes a Z-report;
the cloud stores that frozen copy as-is and never recomputes or edits it. A closed shift is
immutable here: a later upload that tries to change it is ignored, and corrections belong in the
audit log, not in the shift.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import SyncModel


class Shift(SyncModel):
    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed')]

    device = models.ForeignKey('devices.Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='shifts')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text='The cashier, when their login could be matched. owner_username is always kept.',
    )
    owner_username = models.CharField(max_length=150, help_text="The till's record of who opened it (login name).")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    opened_at = models.DateTimeField()
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='counted - expected. Negative = cash short, positive = cash over.',
    )
    z_number = models.PositiveIntegerField(null=True, blank=True, help_text="The till's own Z counter.")
    notes = models.TextField(blank=True, default='')
    report = models.JSONField(null=True, blank=True, help_text='The Z-report exactly as the till froze it at close.')

    class Meta:
        ordering = ['-opened_at']
        indexes = [models.Index(fields=['shop', 'opened_at'])]

    def __str__(self):
        return f'Shift {self.owner_username} {self.opened_at:%Y-%m-%d %H:%M} ({self.status})'


class CashMovement(SyncModel):
    KIND_CHOICES = [('paid_in', 'Paid in'), ('paid_out', 'Paid out')]

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='cash_movements')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['occurred_at']

    def __str__(self):
        return f'{self.kind} {self.amount}: {self.reason}'
