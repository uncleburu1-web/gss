"""The stock ledger: every change to a batch's quantity goes through here and leaves a
StockMovement behind (see inventory.models.StockMovement).

Callers keep doing their own batch arithmetic exactly as before; they add one `record(...)`
call next to it. The two new operations that did not exist before -- recording damage and a
stock count -- live here too, so the web, Android and the till all go through the same rules:

  * damage: any worker, reason required, can only remove stock that is there;
  * stock count: owners / branch managers only (core.permissions.is_owner), reason required.
"""
from decimal import Decimal

from django.db import models
from django.utils import timezone

from .models import StockBatch, StockMovement


class LedgerError(ValueError):
    """A ledger operation the caller asked for cannot be done (shown to the user as-is)."""


def actor_name_for(user):
    if user is None:
        return ''
    worker = getattr(user, 'worker', None)
    return (getattr(worker, 'full_name', '') or user.get_full_name() or user.username)[:150]


def record(shop, batch, quantity, kind, *, item=None, user=None, reason='', reference=None,
           source='web', client_ref=None, system_quantity=None, counted_quantity=None, at=None,
           actor_name=None):
    """Write one movement. `quantity` is signed (+ in, - out). Zero is not recorded."""
    quantity = Decimal(str(quantity))
    if quantity == 0:
        return None
    ref_type, ref_id = reference if reference else ('', '')
    return StockMovement.objects.create(
        shop=shop,
        item=item if item is not None else batch.item,
        variant_id=getattr(batch, 'variant_id', None),
        batch=batch,
        kind=kind,
        quantity=quantity,
        reason=(reason or '')[:255],
        user=user if getattr(user, 'is_authenticated', False) else None,
        actor_name=actor_name if actor_name is not None else actor_name_for(user),
        source=source,
        reference_type=ref_type,
        reference_id=str(ref_id or ''),
        client_ref=client_ref,
        system_quantity=system_quantity,
        counted_quantity=counted_quantity,
        created_at=at or timezone.now(),
    )


def _live_batches(item, variant=None):
    qs = StockBatch.objects.select_for_update().filter(item=item, is_deleted=False)
    if variant is not None:
        qs = qs.filter(variant=variant)
    return qs


def on_hand(item, variant=None):
    qs = StockBatch.objects.filter(item=item, is_deleted=False)
    if variant is not None:
        qs = qs.filter(variant=variant)
    return qs.aggregate(t=models.Sum('quantity_remaining'))['t'] or 0


def _whole(quantity, label):
    """Batch columns hold whole units today (weighed selling is not switched on)."""
    try:
        value = Decimal(str(quantity))
    except Exception:
        raise LedgerError(f'{label} must be a number.')
    if value != value.to_integral_value():
        raise LedgerError(f'{label} must be a whole number for this product.')
    return int(value)


def _take_fefo(item, variant, quantity, kind, **record_kwargs):
    """Remove `quantity` from the item's batches, earliest expiry first; one movement per batch."""
    remaining = quantity
    movements = []
    batches = _live_batches(item, variant).filter(quantity_remaining__gt=0).order_by('expiry_date', 'received_date')
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        batch.save(update_fields=['quantity_remaining', 'updated_at'])
        movements.append(record(batch.shop, batch, -take, kind, item=item, **record_kwargs))
        remaining -= take
    return movements


def record_damage(shop, item, quantity, reason, *, variant=None, user=None, source='web', client_ref=None, at=None,
                  actor_name=None):
    """Write off damaged/expired/lost stock. Must be called inside transaction.atomic()."""
    reason = (reason or '').strip()
    if not reason:
        raise LedgerError('Say why the stock is damaged — a reason is required.')
    qty = _whole(quantity, 'Quantity')
    if qty <= 0:
        raise LedgerError('Quantity must be more than zero.')
    available = sum(b.quantity_remaining for b in _live_batches(item, variant))
    if qty > available:
        raise LedgerError(f'Only {available} of {item.name} in stock — cannot write off {qty}.')
    return _take_fefo(item, variant, qty, StockMovement.DAMAGE, user=user, reason=reason, source=source,
                      client_ref=client_ref, at=at, actor_name=actor_name)


def record_stock_count(shop, item, counted, reason, *, variant=None, user=None, source='web', client_ref=None,
                       at=None, actor_name=None):
    """Set an item's stock to what was physically counted. Must be called inside transaction.atomic().

    A shortfall is taken from the batches earliest-expiry first; a surplus is added to the newest
    live batch (or, if the item has none, to a new 'COUNT' batch at the item's last known cost).
    Each batch touched gets an ADJUSTMENT movement carrying both the system and counted figures.
    """
    reason = (reason or '').strip()
    if not reason:
        raise LedgerError('Say why the count differs — a reason is required.')
    counted_qty = _whole(counted, 'Counted quantity')
    if counted_qty < 0:
        raise LedgerError('Counted quantity cannot be negative.')
    batches = list(_live_batches(item, variant))
    system_qty = sum(b.quantity_remaining for b in batches)
    diff = counted_qty - system_qty
    common = dict(user=user, reason=reason, source=source, client_ref=client_ref, at=at, actor_name=actor_name,
                  system_quantity=system_qty, counted_quantity=counted_qty)
    if diff == 0:
        return []
    if diff < 0:
        return _take_fefo(item, variant, -diff, StockMovement.ADJUSTMENT, **common)

    target = max(batches, key=lambda b: (b.received_date, str(b.id)), default=None)
    if target is None:
        target = StockBatch.objects.create(
            shop=shop, item=item, variant=variant, batch_number='COUNT',
            quantity_received=0, quantity_remaining=0,
            cost_price=Decimal(str(item.cost_price or 0)), selling_price=item.sell_price,
        )
    target.quantity_remaining += diff
    target.save(update_fields=['quantity_remaining', 'updated_at'])
    return [record(shop, target, diff, StockMovement.ADJUSTMENT, item=item, **common)]


def drift(shop=None):
    """Batches whose ledger does not add up to their quantity_remaining.

    Returns [{batch_id, item_id, item_name, quantity_remaining, ledger_total, difference}]. A deleted
    batch is expected to net to zero. Read-only: it reports, it never corrects.
    """
    batches = StockBatch.objects.all()
    if shop is not None:
        batches = batches.filter(shop=shop)
    totals = dict(
        StockMovement.objects.filter(batch__in=batches).values('batch_id')
        .annotate(t=models.Sum('quantity')).values_list('batch_id', 't')
    )
    problems = []
    for batch in batches.select_related('item').iterator():
        expected = Decimal(0) if batch.is_deleted else Decimal(batch.quantity_remaining)
        ledger = totals.get(batch.id) or Decimal(0)
        if ledger != expected:
            problems.append({
                'batch_id': str(batch.id), 'item_id': str(batch.item_id), 'item_name': batch.item.name,
                'shop_id': str(batch.shop_id), 'quantity_remaining': int(expected),
                'ledger_total': str(Decimal(ledger).quantize(Decimal('0.001'))),
                'difference': str((expected - ledger).quantize(Decimal('0.001'))),
            })
    return problems
