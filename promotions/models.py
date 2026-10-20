from decimal import Decimal

from django.db import models

from core.models import SyncModel


class Promotion(SyncModel):
    """A discount rule, kept as data rather than in the checkout screens (SaleFlex's lesson): the
    owner sets it up once, every till downloads it, and the same evaluator (promotions/engine.py here,
    promotions.js on the till and in the apps) works out the discount, so a sale gets the same price
    whether it is rung up offline on a till or online.

      percent_off   value % off each matching line
      amount_off    value naira off each matching unit (never below zero)
      buy_x_get_y   for every buy_quantity + get_quantity of the SAME item, get_quantity are free

    One promotion per line: the one that saves the customer the most. Promotions never stack.
    """
    KIND_CHOICES = [
        ('percent_off', '% off'),
        ('amount_off', 'Amount off each'),
        ('buy_x_get_y', 'Buy X get Y free'),
    ]
    APPLIES_CHOICES = [('all', 'Everything'), ('category', 'A category'), ('item', 'One product')]

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    applies_to = models.CharField(max_length=10, choices=APPLIES_CHOICES, default='item')
    item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, null=True, blank=True, related_name='promotions')
    category = models.CharField(max_length=20, blank=True)
    min_quantity = models.PositiveIntegerField(default=1, help_text='Only when at least this many are bought.')
    buy_quantity = models.PositiveIntegerField(null=True, blank=True)
    get_quantity = models.PositiveIntegerField(null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name
