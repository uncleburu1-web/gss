"""Moving stock between branches of one business (CEO only).

The destination product is found by barcode, then by name (case-insensitive), and created as a
copy of the source product if the branch doesn't stock it yet. Each source batch drawn from becomes
a batch at the destination with the same cost and expiry, so stock value and FEFO carry across.

Tills: the source branch's tills pull the TRANSFER-out movements and deduct; the destination's
tills receive the new batches through the normal batch pull (positive transfer movements are not
sent to tills, or the units would be counted twice). Call inside transaction.atomic().
"""
from django.db.models import Q

import audit

from . import ledger
from .models import InventoryItem, ItemVariant, StockBatch, StockMovement, StockTransfer, StockTransferLine

COPIED_FIELDS = ('name', 'short_code', 'barcode', 'category', 'brand', 'unit', 'spec', 'min_stock',
                 'prescription_required', 'sell_price', 'image_url')


class TransferError(ValueError):
    pass


def _destination_item(item, to_shop):
    match = None
    if item.barcode:
        match = InventoryItem.objects.filter(shop=to_shop, barcode=item.barcode, is_deleted=False).first()
    if match is None:
        match = InventoryItem.objects.filter(shop=to_shop, name__iexact=item.name, is_deleted=False).first()
    if match is None:
        fields = {f: getattr(item, f) for f in COPIED_FIELDS}
        if fields['barcode'] and InventoryItem.objects.filter(shop=to_shop, barcode=fields['barcode'], is_deleted=False).exists():
            fields['barcode'] = None
        match = InventoryItem.objects.create(shop=to_shop, **fields)
    return match


def _destination_variant(variant, to_item):
    if variant is None:
        return None
    found = to_item.variants.filter(size=variant.size, color=variant.color).first()
    if found is None:
        found = ItemVariant.objects.create(shop=to_item.shop, item=to_item, size=variant.size, color=variant.color, sku=variant.sku)
    elif found.is_deleted:
        found.is_deleted = False
        found.save(update_fields=['is_deleted', 'updated_at'])
    return found


def transfer_stock(organization, from_shop, to_shop, lines, *, user, note=''):
    if from_shop.organization_id != organization.id or to_shop.organization_id != organization.id:
        raise TransferError('Both branches must belong to your business.')
    if from_shop.id == to_shop.id:
        raise TransferError('Pick two different branches.')
    if not lines:
        raise TransferError('Add at least one item to move.')

    actor = ledger.actor_name_for(user)
    transfer = StockTransfer.objects.create(organization=organization, from_shop=from_shop, to_shop=to_shop,
                                            note=(note or '')[:255], user=user, actor_name=actor)
    summary = []
    for line in lines:
        item = InventoryItem.objects.select_for_update().filter(id=line.get('item'), shop=from_shop, is_deleted=False).first()
        if item is None:
            raise TransferError('An item is not in the branch you are moving from.')
        variant = None
        if item.has_variants:
            variant = item.variants.filter(id=line.get('variant'), is_deleted=False).first()
            if variant is None:
                raise TransferError(f'{item.name} has sizes/colours — say which one to move.')
        qty = ledger._whole(line.get('quantity'), 'Quantity')
        if qty <= 0:
            raise TransferError('Quantities must be more than zero.')
        batches = StockBatch.objects.select_for_update().filter(item=item, is_deleted=False, quantity_remaining__gt=0)
        if variant is not None:
            batches = batches.filter(variant=variant)
        available = sum(b.quantity_remaining for b in batches)
        if qty > available:
            raise TransferError(f'Only {available} of {item.name} at {from_shop.name}.')

        to_item = _destination_item(item, to_shop)
        to_variant = _destination_variant(variant, to_item)
        remaining = qty
        for batch in batches.order_by('expiry_date', 'received_date'):
            if remaining <= 0:
                break
            take = min(batch.quantity_remaining, remaining)
            batch.quantity_remaining -= take
            batch.save(update_fields=['quantity_remaining', 'updated_at'])
            ledger.record(from_shop, batch, -take, StockMovement.TRANSFER, item=item, user=user,
                          reason=f'Moved to {to_shop.name}', reference=('stock_transfer', transfer.id))
            new_batch = StockBatch.objects.create(
                shop=to_shop, item=to_item, variant=to_variant, batch_number=(f'TRF-{batch.batch_number}' if batch.batch_number else 'TRF')[:60],
                quantity_received=take, quantity_remaining=take, cost_price=batch.cost_price,
                selling_price=batch.selling_price, expiry_date=batch.expiry_date,
                supplier=batch.supplier, supplier_name=batch.supplier_name,
            )
            ledger.record(to_shop, new_batch, take, StockMovement.TRANSFER, item=to_item, user=user,
                          reason=f'Moved from {from_shop.name}', reference=('stock_transfer', transfer.id))
            remaining -= take
        StockTransferLine.objects.create(transfer=transfer, from_item=item, to_item=to_item, from_variant=variant,
                                         to_variant=to_variant, quantity=qty)
        summary.append(f'{qty} x {item.name}{f" ({variant.label})" if variant else ""}')

    for shop, direction in ((from_shop, 'to'), (to_shop, 'from')):
        other = to_shop if direction == 'to' else from_shop
        audit.record('stock.transfer', shop=shop, user=user, target=transfer, reason=note or '',
                     summary=f'Moved {", ".join(summary)} {direction} {other.name}',
                     data={'transfer_id': str(transfer.id), 'from': str(from_shop.id), 'to': str(to_shop.id)})
    return transfer
