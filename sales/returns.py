"""Customer returns: one function used by the web/Android API and by tills (sync), so the rules are
identical everywhere.

  * only a completed (fully paid) sale can be returned against;
  * a reason is always required;
  * never more than was sold minus what was already returned, per line, whole units;
  * the refund is at most what the returned items were sold for (after their share of discount);
  * restocked items go back to the batches they came out of (their SaleAllocation rows), and the
    allocation is reduced either way, so voiding the sale later can't put them back a second time;
  * damaged items (restock = false) are written off: recorded, but not put back in stock.

Call inside transaction.atomic().
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

import audit
from inventory import ledger
from inventory.models import StockBatch, StockMovement

from .models import SalePayment, SaleReturn, SaleReturnLine

CENT = Decimal('0.01')


class ReturnError(ValueError):
    """A return that cannot be accepted; the message is shown to the user as-is."""


def returned_quantity(sale_item):
    return SaleReturnLine.objects.filter(sale_item=sale_item).aggregate(q=Sum('quantity'))['q'] or 0


def _line_value(sale_item, quantity):
    if not sale_item.quantity:
        return Decimal('0.00')
    return (sale_item.total * quantity / sale_item.quantity).quantize(CENT, ROUND_HALF_UP)


def _give_back(sale_item, quantity, restock, *, shop, return_id, user, actor_name, source, at):
    """Reduce the line's allocations by `quantity`; if restocking, put the units back in those batches."""
    remaining = quantity
    for alloc in sale_item.allocations.select_related('batch').order_by('-created_at'):
        if remaining <= 0:
            break
        take = min(alloc.quantity, remaining)
        batch = alloc.batch
        if restock and batch is not None:
            StockBatch.objects.filter(pk=batch.pk).update(quantity_remaining=batch.quantity_remaining + take)
            batch.refresh_from_db(fields=['quantity_remaining'])
            ledger.record(shop, batch, take, StockMovement.RETURN, item=sale_item.item, user=user,
                          actor_name=actor_name, reason='Customer return', source=source,
                          reference=('sale_return', return_id), client_ref=return_id, at=at)
        if take == alloc.quantity:
            alloc.delete()
        else:
            alloc.quantity -= take
            alloc.save(update_fields=['quantity', 'updated_at'])
        remaining -= take
    if restock and remaining > 0 and sale_item.item_id:
        # Units sold beyond what stock records held (an oversell): put them on the newest live batch.
        batch = StockBatch.objects.select_for_update().filter(item_id=sale_item.item_id, is_deleted=False).order_by('-received_date').first()
        if batch is not None:
            StockBatch.objects.filter(pk=batch.pk).update(quantity_remaining=batch.quantity_remaining + remaining)
            ledger.record(shop, batch, remaining, StockMovement.RETURN, item=sale_item.item, user=user,
                          actor_name=actor_name, reason='Customer return', source=source,
                          reference=('sale_return', return_id), client_ref=return_id, at=at)


def process_return(sale, lines, *, reason, refund_method='cash', refund_amount=None, user=None, actor_name=None,
                   source='web', device=None, return_id=None, at=None, shift_ref=None):
    reason = (reason or '').strip()
    if not reason:
        raise ReturnError('Say why the items are being returned — a reason is required.')
    if sale.is_deleted:
        raise ReturnError('This sale was deleted.')
    if sale.status != 'completed':
        raise ReturnError('Only a fully paid sale can take returns. Edit the installment sale instead.')
    if refund_method not in dict(SalePayment.METHOD_CHOICES):
        raise ReturnError('Choose how the money is refunded.')
    if not isinstance(lines, list) or not lines:
        raise ReturnError('Choose at least one item to return.')

    items = {str(i.id): i for i in sale.items.select_for_update().filter(is_deleted=False)}
    wanted = {}
    for line in lines:
        item = items.get(str(line.get('sale_item_id') or line.get('sale_item') or ''))
        if item is None:
            raise ReturnError('A returned item is not on this sale.')
        try:
            qty = ledger.check_quantity(item.item, line.get('quantity'), 'Quantity returned')
        except ledger.LedgerError as exc:
            raise ReturnError(str(exc))
        if qty <= 0:
            raise ReturnError('Return at least some of an item.')
        restock = bool(line.get('restock', True))
        key = (str(item.id), restock)
        wanted[key] = wanted.get(key, 0) + qty

    per_item = {}
    for (item_id, _restock), qty in wanted.items():
        per_item[item_id] = per_item.get(item_id, 0) + qty
    for item_id, qty in per_item.items():
        item = items[item_id]
        left = item.quantity - returned_quantity(item)
        if qty > left:
            raise ReturnError(f'Only {left} of {item.item_name} can still be returned on this sale.')

    total_value = sum((_line_value(items[i], q) for (i, _r), q in wanted.items()), Decimal('0.00'))
    total_cost = sum(((items[i].unit_cost * q).quantize(CENT) for (i, _r), q in wanted.items()), Decimal('0.00'))
    refund = total_value if refund_amount in (None, '') else Decimal(str(refund_amount)).quantize(CENT)
    if refund < 0 or refund > total_value:
        raise ReturnError(f'The refund can be at most {total_value} (what those items were sold for).')

    at = at or timezone.now()
    actor = actor_name if actor_name is not None else ledger.actor_name_for(user if getattr(user, 'is_authenticated', False) else None)
    ret = SaleReturn.objects.create(
        **({'id': return_id} if return_id else {}),
        shop=sale.shop, sale=sale, returned_at=at, reason=reason[:255], refund_method=refund_method,
        refund_amount=refund, total_value=total_value, total_cost=total_cost,
        user=user if getattr(user, 'is_authenticated', False) else None, actor_name=actor[:150],
        source=source, device=device, shift_ref=shift_ref,
    )
    for (item_id, restock), qty in wanted.items():
        item = items[item_id]
        SaleReturnLine.objects.create(sale_return=ret, sale_item=item, quantity=qty, value=_line_value(item, qty),
                                      cost=(item.unit_cost * qty).quantize(CENT), restocked=restock)
        _give_back(item, qty, restock, shop=sale.shop, return_id=ret.id, user=user, actor_name=actor,
                   source=source, at=at)

    names = ', '.join(f'{q} x {items[i].item_name}{"" if r else " (damaged)"}' for (i, r), q in wanted.items())
    audit.record('sale.returned', shop=sale.shop, user=user, target=sale, source=source, device=device,
                 actor_name=actor, reason=reason,
                 summary=f'Return on sale #{sale.invoice_number}: {names}; refunded {refund} by {refund_method}',
                 data={'return_id': str(ret.id), 'value': str(total_value), 'refund': str(refund),
                       'method': refund_method})
    return ret


def returns_between(shops, start, end):
    """Totals of returns recorded in [start, end) for reports: value, cost, refunds, units."""
    qs = SaleReturn.objects.filter(shop__in=shops, returned_at__gte=start, returned_at__lt=end, is_deleted=False)
    agg = qs.aggregate(value=Sum('total_value'), cost=Sum('total_cost'), refunded=Sum('refund_amount'))
    units = SaleReturnLine.objects.filter(sale_return__in=qs).aggregate(q=Sum('quantity'))['q'] or 0
    value = agg['value'] or Decimal('0.00')
    cost = agg['cost'] or Decimal('0.00')
    return {'value': value, 'cost': cost, 'profit': value - cost, 'refunded': agg['refunded'] or Decimal('0.00'),
            'units': units, 'count': qs.count()}
