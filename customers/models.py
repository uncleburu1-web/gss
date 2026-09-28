from django.db import models

from core.models import SyncModel


class Customer(SyncModel):
    """A shop's customer — kept deliberately small (contact details + a
    free-text note) rather than a full CRM. The main payoff of having a
    real record instead of just the free-text `Sale.customer_name` is
    being able to answer "how much does this person owe us across all
    their outstanding sales" (see CustomerViewSet.balance) — useful for a
    shop that sells on credit, which this one does (see Sale.status
    'outstanding' / installment payments).
    """
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
