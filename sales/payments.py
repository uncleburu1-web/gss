"""Payments are the source of truth for what was paid on a sale (stage 2).

Every code path that takes money on a sale (REST create, add-payment, replace-item, and both
sync protocols) writes SalePayment rows and then calls recompute_paid(); nothing writes
Sale.amount_paid from a client's absolute number any more. The invariant every path keeps:

    sale.amount_paid == sum(non-deleted payments)
"""
import uuid
from decimal import Decimal

from django.db.models import Sum

from .models import SalePayment

# Deterministic id of the one "legacy" lump payment a sale may carry: the backfill and older
# tills (which only ever send an absolute amount_paid) both use it, so repeating either is
# idempotent and the two never create two rows for the same money.
_LEGACY_NS = uuid.UUID('6f0b8a52-6d5e-4a3e-9a63-2b7e1c4f0d11')


def legacy_payment_id(sale_id):
    return uuid.uuid5(_LEGACY_NS, f'legacy:{sale_id}')


def paid_total(sale):
    total = SalePayment.objects.filter(sale=sale, is_deleted=False).aggregate(s=Sum('amount'))['s']
    return total or Decimal('0.00')


def recompute_paid(sale):
    """Set amount_paid from the payments; a sale whose payments cover its total is completed.
    Never flips a completed sale back to outstanding (only a refund flow may decide that)."""
    paid = paid_total(sale)
    sale.amount_paid = paid
    if sale.status != 'completed' and paid >= sale.total and sale.total > 0:
        sale.status = 'completed'
    sale.save(update_fields=['amount_paid', 'status', 'updated_at'])
    return sale


def record_payment(sale, amount, method=None, *, source, paid_at=None, payment_id=None, tendered=None,
                   change_given=None, reference='', shift_ref=None, device=None, user=None):
    """Create one payment (idempotent when payment_id is given) and recompute the sale.
    Returns (payment, created)."""
    method = method or sale.payment_method or 'cash'
    if method not in dict(SalePayment.METHOD_CHOICES):
        method = 'other'
    defaults = {
        'shop': sale.shop, 'sale': sale, 'method': method, 'amount': Decimal(str(amount)),
        'tendered': None if tendered in (None, '') else Decimal(str(tendered)),
        'change_given': Decimal(str(change_given or 0)), 'reference': (reference or '')[:100],
        'source': source, 'shift_ref': shift_ref or None, 'device': device, 'recorded_by': user,
    }
    if paid_at is not None:
        defaults['paid_at'] = paid_at
    if payment_id is None:
        payment, created = SalePayment.objects.create(**defaults), True
    else:
        payment, created = SalePayment.objects.get_or_create(id=payment_id, shop=sale.shop, defaults=defaults)
        if not created and payment.sale_id != sale.id:
            raise ValueError('That payment id already belongs to a different sale.')
    recompute_paid(sale)
    return payment, created


def apply_legacy_amount_paid(sale, amount_paid):
    """An older client only knows an ABSOLUTE amount_paid. Fold it into the one legacy payment so
    the invariant holds. Money already recorded in the cloud is never reduced by it: the result is
    max(what the client says, what the cloud already has). That is what stops an offline till's
    stale total from erasing a payment taken on the web meanwhile."""
    target = max(Decimal(str(amount_paid or 0)), paid_total(sale))
    legacy_id = legacy_payment_id(sale.id)
    others = SalePayment.objects.filter(sale=sale, is_deleted=False).exclude(id=legacy_id) \
        .aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    legacy_amount = target - others
    existing = SalePayment.objects.filter(id=legacy_id).first()
    if existing is not None:
        if existing.amount != legacy_amount:
            existing.amount = legacy_amount
            existing.save(update_fields=['amount', 'updated_at'])
    elif legacy_amount != 0:
        SalePayment.objects.create(
            id=legacy_id, shop=sale.shop, sale=sale, method=sale.payment_method or 'cash',
            amount=legacy_amount, paid_at=sale.date, source='legacy',
        )
    return recompute_paid(sale)


def serialize_payment(p):
    return {
        'id': str(p.id), 'sale_id': str(p.sale_id), 'method': p.method, 'amount': str(p.amount),
        'tendered': None if p.tendered is None else str(p.tendered), 'change_given': str(p.change_given),
        'reference': p.reference, 'paid_at': p.paid_at.isoformat(), 'source': p.source,
        'shift_id': str(p.shift_ref) if p.shift_ref else None,
    }
