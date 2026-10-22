"""
Sync app — the cloud side of the desktop's offline-first outbox.

The desktop (`desktop/src/main/sync.js`) has been fully built and pointed
at `/api/sync/push/` since it was written — every product, stock batch,
and sale rung up offline gets queued locally and retried every 15s. This
endpoint not existing yet was the entire reason none of it ever reached
the cloud: every push got a 404, which sync.js's own error handling
correctly treats as "still offline," so it just quietly kept the queue
`pending` forever instead of failing loudly. Nothing below changes that
contract — same request/response shape sync.js already sends and expects.
"""
import hashlib
from decimal import Decimal
import json
import uuid
from datetime import timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.modules import branch_modules
from core.permissions import is_owner
from core.capabilities import has_capability
from core.utils import get_shop_for_user, user_may_use_branch_device
from customers.models import Customer
from devices.models import Device
import audit
from inventory import ledger
from inventory.models import InventoryItem, StockBatch, StockMovement
from realtime.events import broadcast
from sales.models import Sale, SaleItem, next_invoice_number
from restaurant import ingest as restaurant_ingest
from restaurant import sync as restaurant_sync
from restaurant.models import Order
from promotions.engine import as_dict as _promotion_as_dict
from promotions.models import Promotion
from shifts.models import CashMovement, Shift
from sales.payments import apply_legacy_amount_paid, record_payment, recompute_paid, serialize_payment
from sales.views import _allocate_stock, _restore_stock

from .models import SyncOperation


class _Skip(Exception):
    """Raised when an operation can't be applied yet because something it
    depends on (a sale_item's Sale header, a stock_batch's product) hasn't
    landed in this or an earlier batch. This is NOT a rejection: the
    caller leaves the op out of `results` entirely, and sync.js already
    treats an id missing from `results` as "retry next tick" — so a
    sale_item that arrives one batch ahead of its Sale (shouldn't happen
    given queue ordering, but the network can still reorder retries)
    resolves itself automatically instead of being permanently dropped.
    """


# --- one handler per entity_type sync.js can send --------------------------

def _is_repeat_create(model, shop, entity_id, operation):
    """A 'create' for a row the cloud already has is a RE-SEND, never new information: tills
    only ever create these rows once, under a fresh id. Older tills re-sent their whole history
    as creates at every startup (desktop reconcileUnsyncedRows); applying those overwrote newer
    cloud values (an owner's price change, a batch's real remaining stock) with stale copies.
    So a repeated create changes nothing."""
    return operation == 'create' and model.objects.filter(id=entity_id, shop=shop).exists()


_PRODUCT_AUDITED = ('name', 'sell_price', 'barcode', 'category', 'min_stock', 'unit')


def _apply_product(shop, entity_id, operation, payload, ctx=None):
    ctx = ctx or {}
    if operation == 'delete':
        item = InventoryItem.objects.filter(id=entity_id, shop=shop, is_deleted=False).first()
        InventoryItem.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        if item is not None:
            audit.record('product.deleted', shop=shop, user=ctx.get('user'), target=item, source='till',
                         device=ctx.get('device'), summary=f'Deleted product {item.name} on a till')
        return
    if _is_repeat_create(InventoryItem, shop, entity_id, operation):
        return
    barcode = payload.get('barcode') or None
    # Barcodes are unique per branch (not globally) -- reject a clash with a
    # clear message here instead of letting the DB constraint surface as a
    # raw IntegrityError string in the sync results.
    if barcode and InventoryItem.objects.filter(
        shop=shop, barcode=barcode, is_deleted=False,
    ).exclude(id=entity_id).exists():
        raise ValueError('Another product in this branch already uses that barcode.')
    defaults = {
        'name': payload.get('name', ''),
        'short_code': payload.get('short_code') or '',
        'barcode': barcode,
        'category': payload.get('category') or 'other',
        'unit': payload.get('unit') or 'PIECE',
        'sell_price': payload.get('sell_price') or 0,
        'min_stock': payload.get('min_stock') if payload.get('min_stock') is not None else 2,
    }
    if 'sold_by_weight' in payload:  # only when the till knows about weighed products
        defaults['sold_by_weight'] = bool(payload.get('sold_by_weight'))
    # image_url is deliberately only touched when the payload actually
    # names it. It's real product data (see InventoryItem.image_url) and
    # `image_url: null` IS a valid, expected value for a product with no
    # photo -- but the image itself is only ever set through the
    # dedicated Cloudinary upload endpoint (InventoryItemViewSet.image),
    # never through this JSON sync path. An older desktop build's local
    # row (before it has the image_url column at all) simply omits the
    # key here, and that must leave an already-uploaded image alone
    # rather than reading as "clear it" -- hence the explicit `in`
    # check instead of always defaulting to payload.get('image_url').
    if 'image_url' in payload:
        defaults['image_url'] = payload.get('image_url') or None
    existing = InventoryItem.objects.filter(id=entity_id, shop=shop).first()
    before = {f: getattr(existing, f) for f in _PRODUCT_AUDITED} if existing else None
    item, _ = InventoryItem.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)
    if before is not None:
        item.refresh_from_db()  # compare stored values (900 vs 900.00 is not a change)
        diff = audit.changes(before, {f: getattr(item, f) for f in _PRODUCT_AUDITED}, _PRODUCT_AUDITED)
        if diff:
            audit.record('product.price_changed' if 'sell_price' in diff else 'product.changed', shop=shop,
                         user=ctx.get('user'), target=item, source='till', device=ctx.get('device'),
                         summary=f'{item.name}: ' + ', '.join(f'{k} {a} -> {b}' for k, (a, b) in diff.items()),
                         data={'changes': diff})


def _apply_stock_batch(shop, entity_id, operation, payload, ctx=None):
    if operation == 'delete':
        StockBatch.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return
    if _is_repeat_create(StockBatch, shop, entity_id, operation):
        return  # above all: never let a till's copy overwrite the cloud's quantity_remaining
    try:
        item = InventoryItem.objects.get(id=payload['product_id'], shop=shop)
    except (InventoryItem.DoesNotExist, KeyError):
        raise _Skip()  # the product this batch belongs to hasn't synced yet

    # The desktop's local schema doesn't carry a per-batch selling_price
    # (only cost_price) — it keeps one sell_price on the product itself.
    # StockBatch.selling_price is required cloud-side, so fall back to the
    # item's current price rather than rejecting the whole batch over a
    # field the desktop was never asked to track.
    selling_price = payload.get('selling_price')
    if selling_price in (None, ''):
        selling_price = item.sell_price

    defaults = {
        'item': item,
        'batch_number': payload.get('batch_number') or '',
        'quantity_received': _qty(payload.get('quantity_received')),
        'quantity_remaining': _qty(payload.get('quantity_remaining')),
        'cost_price': payload.get('cost_price') or 0,
        'selling_price': selling_price,
        'expiry_date': payload.get('expiry_date') or None,
    }
    before = StockBatch.objects.filter(id=entity_id, shop=shop).values_list('quantity_remaining', flat=True).first()
    batch, created = StockBatch.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)
    user = (ctx or {}).get('user')
    if created:
        ledger.record(shop, batch, batch.quantity_remaining, StockMovement.RECEIPT, user=user, source='till',
                      reference=('stock_batch', batch.id))
    elif before is not None and batch.quantity_remaining != before:
        ledger.record(shop, batch, batch.quantity_remaining - before, StockMovement.ADJUSTMENT, user=user,
                      source='till', reason='Batch edited on a till', reference=('stock_batch', batch.id))


def _apply_customer(shop, entity_id, operation, payload, ctx=None):
    if operation == 'delete':
        Customer.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return
    if _is_repeat_create(Customer, shop, entity_id, operation):
        return
    defaults = {
        'name': payload.get('name', ''),
        'phone': payload.get('phone') or '',
        'email': payload.get('email') or '',
        'address': payload.get('address') or '',
        'notes': payload.get('notes') or '',
    }
    Customer.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)


def _apply_sale(shop, entity_id, operation, payload, ctx=None):
    if operation == 'delete':
        try:
            sale = Sale.objects.get(id=entity_id, shop=shop)
        except Sale.DoesNotExist:
            return  # never arrived and now it's deleted too — nothing to do
        if sale.returns.filter(is_deleted=False).exists():
            raise ValueError('This sale has returns recorded against it, so it cannot be deleted.')
        for sale_item in sale.items.all():
            _restore_stock(sale_item, source='till', user=(ctx or {}).get('user'))
            sale_item.is_deleted = True
            sale_item.save(update_fields=['is_deleted', 'updated_at'])
        sale.payments.update(is_deleted=True)
        sale.is_deleted = True
        sale.save(update_fields=['is_deleted', 'updated_at'])
        audit.record('sale.deleted', shop=shop, user=(ctx or {}).get('user'), target=sale, source='till',
                     device=(ctx or {}).get('device'),
                     summary=f'Deleted sale #{sale.invoice_number} ({sale.total}) on a till')
        return
    if _is_repeat_create(Sale, shop, entity_id, operation):
        return  # a re-sent header must not reset a status or customer changed since

    defaults = {
        'customer_name': payload.get('customer_name') or 'Walk-in',
        'staff_name': payload.get('staff_name') or '',
        'payment_method': payload.get('payment_method') or 'cash',
        'status': payload.get('status') or 'completed',
    }
    customer_id = payload.get('customer_id')
    if customer_id:
        customer = Customer.objects.filter(id=customer_id, shop=shop).first()
        if customer is None:
            raise _Skip()  # this sale's customer hasn't synced yet
        defaults['customer'] = customer
    # Record WHEN the sale happened, not when it finally reached us -- but only
    # when the sale is being created. A later update (e.g. an installment
    # payment) must never be able to rewrite a sale's date.
    if not Sale.objects.filter(id=entity_id, shop=shop).exists():
        sale_date = _sale_date_from_payload(payload)
        if sale_date is not None:
            defaults['date'] = sale_date
        if payload.get('shift_id'):
            defaults['shift_ref'] = _uuid_or_none(payload.get('shift_id'))
        worker = getattr((ctx or {}).get('user'), 'worker', None)
        if worker is not None:
            defaults['worker'] = worker  # the signed-in seller who pushed it rang it up
    # The till's printed receipt number. Only written when sent, so an update from an older client that
    # doesn't know about it can never blank one that is already there; trimmed rather than rejected.
    receipt_no = payload.get('receipt_no')
    if receipt_no:
        defaults['receipt_no'] = str(receipt_no)[:40]
    invoice_number = payload.get('invoice_number')
    if invoice_number:
        # The desktop sends its own offline-assigned number (see the
        # field's help_text on the model) — always trust it when present,
        # create or update, so a payment-update push never renumbers a
        # sale that already has one.
        defaults['invoice_number'] = invoice_number
    elif operation == 'create' and not Sale.objects.filter(id=entity_id, shop=shop).exists():
        # Only assign a fresh cloud number on a genuine first-time create
        # from an older client that never sent one — never on an update,
        # which would silently renumber an already-numbered sale.
        defaults['invoice_number'] = next_invoice_number(shop)
    sale, _created = Sale.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)
    # An older till sends the ABSOLUTE amount paid. It becomes the sale's one legacy payment, and it
    # can never reduce money the cloud already has (e.g. an installment taken on the web meanwhile).
    apply_legacy_amount_paid(sale, payload.get('amount_paid') or 0)


def _apply_sale_item(shop, entity_id, operation, payload, ctx=None):
    if operation == 'delete':
        SaleItem.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return

    try:
        sale = Sale.objects.get(id=payload['sale_id'], shop=shop)
    except (Sale.DoesNotExist, KeyError):
        raise _Skip()  # this line's Sale header hasn't synced yet

    item = None
    product_id = payload.get('product_id')
    if product_id:
        item = InventoryItem.objects.filter(id=product_id, shop=shop).first()
        if item is None:
            raise _Skip()  # the product this line sold hasn't synced yet

    defaults = {
        'sale': sale,
        'item': item,
        'item_name': payload.get('item_name', ''),
        'category': payload.get('category') or '',
        'quantity': _qty(payload.get('quantity'), 1),
        'unit_price': payload.get('unit_price') or 0,
        'discount': payload.get('discount') or 0,
    }
    sale_item, created = SaleItem.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)

    if created and item is not None:
        # Deliberately NOT the unit_cost the desktop computed locally —
        # that was FEFO'd against the desktop's own SQLite batches. The
        # cloud independently FEFO-allocates against ITS batches, same
        # function the normal REST create-sale endpoint uses, so cost/
        # profit numbers here match what they'd be had this sale happened
        # online in the first place.
        _allocate_stock(sale_item, shop, source='till')

    if sale.status == 'completed':
        apply_legacy_amount_paid(sale, sale.total)


def _qty(value, default=0):
    """A quantity from a till (a float for kg) as an exact 3-decimal Decimal."""
    if value in (None, ''):
        value = default
    return Decimal(str(value)).quantize(Decimal('0.001'))


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _sale_header_defaults(shop, entity_id, payload, creating):
    defaults = {
        'customer_name': payload.get('customer_name') or 'Walk-in',
        'staff_name': payload.get('staff_name') or '',
        'payment_method': payload.get('payment_method') or 'cash',
        'status': payload.get('status') or 'completed',
    }
    receipt_no = payload.get('receipt_no')
    if receipt_no:
        defaults['receipt_no'] = str(receipt_no)[:40]
    if payload.get('invoice_number'):
        defaults['invoice_number'] = payload['invoice_number']
    elif creating:
        defaults['invoice_number'] = next_invoice_number(shop)
    if creating:
        sale_date = _sale_date_from_payload(payload)
        if sale_date is not None:
            defaults['date'] = sale_date
    return defaults


def _apply_sale_doc(shop, entity_id, operation, payload, ctx=None):
    """A whole sale in ONE operation (sync protocol 2): header, every line, every payment.

    Runs inside the caller's transaction together with the SyncOperation claim, so the cloud
    either has the complete sale (lines, stock allocations, payments) or nothing of it. Before
    this, the header and each line were separate operations in separate transactions, and a
    report could see a sale with only some of its lines (G1). Every part is keyed by the till's
    own ids, so a replay creates nothing twice."""
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('A sale_doc can only be created; changes travel as their own operations.')
    items = payload.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('A sale must have at least one line.')
    payments = payload.get('payments') or []
    if not isinstance(payments, list):
        raise ValueError('payments must be a list.')

    # Everything this sale points at must already be in the cloud; otherwise wait (not a rejection).
    customer = None
    if payload.get('customer_id'):
        customer = Customer.objects.filter(id=payload['customer_id'], shop=shop).first()
        if customer is None:
            raise _Skip()
    products = {}
    for line in items:
        if not isinstance(line, dict) or not line.get('id'):
            raise ValueError('Every sale line needs its own id.')
        product_id = line.get('product_id')
        if product_id and product_id not in products:
            product = InventoryItem.objects.filter(id=product_id, shop=shop).first()
            if product is None:
                raise _Skip()
            products[product_id] = product

    creating = not Sale.objects.filter(id=entity_id, shop=shop).exists()
    defaults = _sale_header_defaults(shop, entity_id, payload, creating)
    if customer is not None:
        defaults['customer'] = customer
    if creating:
        defaults['device'] = ctx.get('device')
        defaults['received_at'] = timezone.now()
        defaults['shift_ref'] = _uuid_or_none(payload.get('shift_id'))
        worker = getattr(ctx.get('user'), 'worker', None)
        if worker is not None:
            defaults['worker'] = worker
    if creating:
        sale = Sale.objects.create(id=entity_id, shop=shop, **defaults)
    else:
        # Already here (a re-send, or the old protocol got it first): keep the cloud's header as it
        # is and only add any line or payment it does not have yet.
        sale = Sale.objects.get(id=entity_id, shop=shop)

    for line in items:
        existing = SaleItem.objects.filter(id=line['id']).first()
        if existing is not None:
            if existing.sale_id != sale.id:
                raise ValueError('A sale line id is already used by a different sale.')
            continue  # already applied: never allocate stock twice
        item = products.get(line.get('product_id')) if line.get('product_id') else None
        promotion = None
        if line.get('promotion_id'):
            promotion = Promotion.objects.filter(id=_uuid_or_none(line['promotion_id']), shop=shop).first()
        sale_item = SaleItem.objects.create(
            id=line['id'], shop=shop, sale=sale, item=item,
            item_name=line.get('item_name', ''), category=line.get('category') or '',
            quantity=_qty(line.get('quantity'), 1), unit_price=line.get('unit_price') or 0,
            discount=line.get('discount') or 0, promotion=promotion,
        )
        if item is not None:
            _allocate_stock(sale_item, shop, source='till')  # the cloud's own FEFO, as for every other sale

    if creating:
        _audit_discount(sale, ctx, source='till')

    for p in payments:
        if not isinstance(p, dict) or not p.get('id'):
            raise ValueError('Every payment needs its own id.')
        record_payment(
            sale, p.get('amount') or 0, p.get('method'), source='till', payment_id=p['id'],
            paid_at=parse_datetime(p['paid_at']) if isinstance(p.get('paid_at'), str) else None,
            tendered=p.get('tendered'), change_given=p.get('change_given'), reference=p.get('reference') or '',
            shift_ref=_uuid_or_none(p.get('shift_id')), device=ctx.get('device'), user=ctx.get('user'),
        )
    recompute_paid(sale)


def _audit_discount(sale, ctx, source):
    """A sale rung up with any discount leaves an audit line: who gave how much off what."""
    # Promotion discounts are the owner's own rules, not a cashier's decision: not audited per sale.
    lines = [(i.item_name, i.discount, i.quantity * i.unit_price) for i in sale.items.all() if i.discount and not i.promotion_id]
    if not lines:
        return
    total = sum(d for _, d, _ in lines)
    audit.record('sale.discount', shop=sale.shop, user=ctx.get('user'), target=sale, source=source,
                 device=ctx.get('device'), actor_name=sale.staff_name or None,
                 summary=f'Discount of {total} on sale #{sale.invoice_number} '
                         f'({", ".join(f"{n}: {d}" for n, d, _ in lines)})',
                 data={'total_discount': str(total),
                       'lines': [{'item': n, 'discount': str(d), 'before_discount': str(g)} for n, d, g in lines]})


# Things a till records that only matter for the audit trail (protocol 5). Append-only; only these
# actions are accepted, so a till cannot write arbitrary audit lines.
TILL_AUDIT_ACTIONS = {
    'sync.retry': 'Retried changes that had failed to sync',
    'sale.voided_on_till': 'Voided a sale on the till',
    'drawer.opened': 'Opened the cash drawer without a sale',
}


def _apply_audit_event(shop, entity_id, operation, payload, ctx=None):
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('An audit event can only be created.')
    from audit.models import AuditLog
    if AuditLog.objects.filter(shop=shop, target_type='till_event', target_id=str(entity_id)).exists():
        return
    action = payload.get('action')
    if action in restaurant_ingest.AUDIT_ACTIONS:
        restaurant_ingest.require_hub(shop, ctx)  # restaurant entries only from the branch's hub (protocol 6)
        known = restaurant_ingest.AUDIT_ACTIONS
    elif action in TILL_AUDIT_ACTIONS:
        known = TILL_AUDIT_ACTIONS
    else:
        raise ValueError('Unknown audit action.')
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    entry = audit.record(action, shop=shop, user=ctx.get('user'), source='till', device=ctx.get('device'),
                         target_type='till_event', target_id=str(entity_id),
                         actor_name=(payload.get('actor') or None),
                         summary=str(payload.get('summary') or known[action])[:255],
                         reason=str(payload.get('reason') or '')[:255], data=data)
    at = _parse_dt(payload.get('created_at'))
    if at is not None and at <= timezone.now():
        AuditLog.objects.filter(pk=entry.pk).update(at=at)  # when it happened on the till, not when it arrived


def _apply_sale_return(shop, entity_id, operation, payload, ctx=None):
    """Items returned on a till (protocol 5). Same rules as the web (sales.returns.process_return);
    the return keeps the till's id, which is how a re-send and the till's own stock are recognised."""
    from sales.models import SaleReturn
    from sales.returns import ReturnError, process_return
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('A return can only be created.')
    if SaleReturn.objects.filter(id=entity_id).exists():
        return
    sale = Sale.objects.select_for_update().filter(id=payload.get('sale_id'), shop=shop).first()
    if sale is None:
        raise _Skip()  # the sale has not arrived yet
    user = ctx.get('user')
    if not has_capability(user, 'process_return'):
        raise ValueError("Your role can't take returns — ask an owner or manager.")
    at = _parse_dt(payload.get('created_at'))
    if at is not None and at > timezone.now() + MAX_CLIENT_CLOCK_AHEAD:
        at = None
    try:
        process_return(
            sale, payload.get('lines') or [], reason=payload.get('reason') or '',
            refund_method=payload.get('refund_method') or 'cash', refund_amount=payload.get('refund_amount'),
            user=user, actor_name=payload.get('actor') or None, source='till', device=ctx.get('device'),
            return_id=entity_id, at=at, shift_ref=_uuid_or_none(payload.get('shift_id')),
        )
    except ReturnError as exc:
        raise ValueError(str(exc))


def _apply_sale_payment(shop, entity_id, operation, payload, ctx=None):
    """One payment taken on the till against an existing sale (an installment). Append-only:
    the cloud ADDS it, so a payment taken on the web meanwhile is never overwritten (G2)."""
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('A payment can only be created.')
    sale = Sale.objects.filter(id=payload.get('sale_id'), shop=shop).first()
    if sale is None:
        raise _Skip()  # its sale hasn't arrived yet
    if sale.is_deleted:
        raise ValueError('That sale was deleted, so the payment was not recorded.')
    record_payment(
        sale, payload.get('amount') or 0, payload.get('method'), source='till', payment_id=entity_id,
        paid_at=parse_datetime(payload['paid_at']) if isinstance(payload.get('paid_at'), str) else None,
        tendered=payload.get('tendered'), change_given=payload.get('change_given'),
        reference=payload.get('reference') or '', shift_ref=_uuid_or_none(payload.get('shift_id')),
        device=ctx.get('device'), user=ctx.get('user'),
    )


def _parse_dt(value):
    if not isinstance(value, str) or not value:
        return None
    parsed = parse_datetime(value)
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _shift_cashier(shop, username, ctx):
    """The login behind a till's shift owner name, if it belongs to this branch (else None)."""
    user = ctx.get('user')
    if user is not None and username and user.get_username() == username:
        return user
    from staff.models import Worker
    worker = Worker.objects.select_related('user').filter(shop=shop, user__username=username).first()
    return worker.user if worker else None


def _apply_shift(shop, entity_id, operation, payload, ctx=None):
    """A till's cashier shift (stage 3), sent when it opens and again when it closes.

    Upsert by the till's shift id, so a close that arrives first (a till that upgraded mid-shift)
    still lands. Once the cloud has a CLOSED shift it never changes again: a Z-report is a frozen
    record, and corrections belong in the audit log."""
    ctx = ctx or {}
    if operation == 'delete':
        raise ValueError('A shift cannot be deleted.')
    existing = Shift.objects.filter(id=entity_id, shop=shop).first()
    if existing is not None and existing.status == 'closed':
        return
    owner = str(payload.get('owner') or '')[:150]
    opened_at = _parse_dt(payload.get('opened_at'))
    if not owner or opened_at is None:
        raise ValueError('A shift needs its owner and opening time.')
    report = payload.get('report_json')
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            report = None
    closed = payload.get('status') == 'closed'
    defaults = {
        'owner_username': owner,
        'user': _shift_cashier(shop, owner, ctx),
        'status': 'closed' if closed else 'open',
        'opened_at': opened_at,
        'opening_float': payload.get('opening_float') or 0,
        'closed_at': _parse_dt(payload.get('closed_at')) if closed else None,
        'expected_cash': payload.get('expected_cash') if closed else None,
        'counted_cash': payload.get('counted_cash') if closed else None,
        'variance': payload.get('variance') if closed else None,
        'z_number': payload.get('z_number') if closed else None,
        'notes': str(payload.get('notes') or ''),
        'report': report if closed else None,
    }
    if existing is None:
        defaults['device'] = ctx.get('device')
    shift, _ = Shift.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)
    if closed:
        audit.record('shift.closed', shop=shop, user=shift.user, target=shift, source='till', device=ctx.get('device'),
                     actor_name=shift.owner_username,
                     summary=f'Z-report #{shift.z_number}: expected {shift.expected_cash}, counted {shift.counted_cash}, '
                             f'variance {shift.variance}',
                     data={'variance': str(shift.variance), 'expected': str(shift.expected_cash),
                           'counted': str(shift.counted_cash)})


def _apply_cash_movement(shop, entity_id, operation, payload, ctx=None):
    """Cash paid into or out of a shift's drawer, with its reason. Append-only."""
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('A cash movement can only be created.')
    if CashMovement.objects.filter(id=entity_id, shop=shop).exists():
        return
    shift = Shift.objects.filter(id=payload.get('shift_id'), shop=shop).first()
    if shift is None:
        raise _Skip()  # its shift has not arrived yet
    if payload.get('kind') not in ('paid_in', 'paid_out'):
        raise ValueError('Unknown cash movement kind.')
    occurred_at = _parse_dt(payload.get('created_at')) or timezone.now()
    CashMovement.objects.create(
        id=entity_id, shop=shop, shift=shift, kind=payload['kind'], amount=payload.get('amount') or 0,
        reason=str(payload.get('reason') or '')[:255], occurred_at=occurred_at, user=shift.user,
    )


def _apply_stock_movement(shop, entity_id, operation, payload, ctx=None):
    """Damage or a stock count recorded on a till (protocol 4).

    entity_id is the till's document id; every movement it produces here carries it as
    client_ref, which is how a repeat is recognised and how the till knows, on its next pull,
    that these rows are its own and already applied locally.

      damage:     {kind, product_id, variant_id?, quantity, reason, created_at, actor}
      adjustment: {kind, product_id, variant_id?, counted_quantity, reason, created_at, actor}

    Damage: any worker, reason required. The till already took the goods out of its own stock,
    so if other tills have meanwhile sold some of them, the cloud writes off what it still has
    rather than refusing (the goods are gone either way). A stock count: owners only.
    """
    ctx = ctx or {}
    if operation != 'create':
        raise ValueError('A stock movement can only be created.')
    if StockMovement.objects.filter(shop=shop, client_ref=entity_id).exists():
        return
    try:
        item = InventoryItem.objects.select_for_update().get(id=payload.get('product_id'), shop=shop)
    except (InventoryItem.DoesNotExist, ValueError, DjangoValidationError):
        raise _Skip()  # the product has not arrived yet
    variant = None
    if payload.get('variant_id'):
        variant = item.variants.filter(id=payload['variant_id']).first()
    user = ctx.get('user')
    common = dict(variant=variant, user=user, source='till', client_ref=entity_id,
                  at=_parse_dt(payload.get('created_at')), actor_name=(payload.get('actor') or None))
    kind = payload.get('kind')
    if kind == 'damage':
        available = ledger.on_hand(item, variant)
        wanted = ledger.check_quantity(item, payload.get('quantity') or 0, 'Quantity')
        quantity = min(wanted, available)
        if quantity <= 0:
            return  # nothing left on the cloud's side to write off
        ledger.record_damage(shop, item, quantity, payload.get('reason') or '', **common)
        audit.record('stock.damage', shop=shop, user=user, target=item, source='till', device=ctx.get('device'),
                     actor_name=payload.get('actor') or None, reason=payload.get('reason') or '',
                     summary=f'Wrote off {quantity} x {item.name} as damaged', data={'quantity': quantity})
    elif kind == 'adjustment':
        if not is_owner(user):
            raise ValueError('Only an owner can adjust stock after a count.')
        moves = ledger.record_stock_count(shop, item, payload.get('counted_quantity'), payload.get('reason') or '', **common)
        if moves:
            audit.record('stock.count', shop=shop, user=user, target=item, source='till', device=ctx.get('device'),
                         actor_name=payload.get('actor') or None, reason=payload.get('reason') or '',
                         summary=f'Stock count of {item.name}: system {moves[0].system_quantity}, counted {moves[0].counted_quantity}',
                         data={'system': str(moves[0].system_quantity), 'counted': str(moves[0].counted_quantity)})
    else:
        raise ValueError('Unknown stock movement kind.')


# What this cloud understands. Sent with every pull so a till only switches to newer operation
# types once the cloud it talks to can apply them.
#   2: sale_doc, sale_payment          3: shift, cash_movement
#   4: stock_movement (damage, stock count) both ways
#   5: audit_event, mobile money payments, sale_return
#   6: restaurant: order_doc (a finished order, one piece), menu_availability, restaurant audit
#      actions -- all only from the branch's hub (restaurant/ingest.py)
SYNC_PROTOCOL = 6

_HANDLERS = {
    'product': _apply_product,
    'stock_batch': _apply_stock_batch,
    'customer': _apply_customer,
    'sale': _apply_sale,
    'sale_item': _apply_sale_item,
    'sale_doc': _apply_sale_doc,
    'sale_payment': _apply_sale_payment,
    'shift': _apply_shift,
    'cash_movement': _apply_cash_movement,
    'stock_movement': _apply_stock_movement,
    'audit_event': _apply_audit_event,
    'sale_return': _apply_sale_return,
    'order_doc': restaurant_ingest.apply_order_doc,
    'menu_availability': restaurant_ingest.apply_menu_availability,
}

# Same events a REST create/update/delete on the equivalent resource would
# fire (see core.mixins.ShopScopedMixin) — unified so the frontend listens
# for ONE set of event names regardless of whether a change came from the
# web (REST) or a desktop sync push. sync.js's names ('product',
# 'stock_batch', 'create'...) don't match Django's model_name/DRF-action
# naming on their own, so this just translates between the two vocabularies.
_EVENT_ENTITY = {
    'product': 'inventoryitem', 'stock_batch': 'stockbatch',
    'customer': 'customer', 'sale': 'sale', 'sale_item': 'saleitem',
    'sale_doc': 'sale', 'sale_payment': 'sale',
    'shift': 'shift', 'cash_movement': 'cashmovement', 'stock_movement': 'stockmovement',
    'sale_return': 'sale', 'audit_event': 'auditlog',
}
_EVENT_VERB = {'create': 'created', 'update': 'updated', 'delete': 'deleted'}

# Same split as core.permissions.IsOwnerOrReadOnly, which gates these same
# writes on web/Android's REST endpoints (InventoryItemViewSet/
# StockBatchViewSet) — a seller can read products and stock either way,
# but only an owner can create/edit/delete them. Without this check here
# too, a seller's desktop till could push a product or stock-batch write
# straight through the sync queue even with the UI buttons for it hidden
# (see desktop/src/renderer/ProductsScreen.jsx), since this endpoint would
# otherwise only require *any* authenticated device on the right shop.
# Sale/sale_item/customer stay open to every role, exactly as on web.
_OWNER_ONLY_ENTITIES = {'product', 'stock_batch'}
_REPEAT_MODELS = {'product': InventoryItem, 'stock_batch': StockBatch}


class _Duplicate(Exception):
    """Our idempotency CLAIM (the SyncOperation insert) lost a race: another
    request inserted the same operation id between our pre-check and our
    insert. Not an error -- it means "somebody else is applying (or has
    applied) this exact operation", which is precisely what idempotency
    promises to tolerate."""


DEVICE_HEADER = 'X-Device-ID'


class _DeviceRemoved(APIException):
    status_code = 410
    default_detail = 'This device was removed from the shop. Sign in again to pair it.'
    default_code = 'device_removed'


def resolve_sync_scope(request):
    """(shop, device) for a sync request.

    A till sends X-Device-ID. Its branch is the one the device was PAIRED to at
    login (core.auth_serializers), never something the request names, and the
    signed-in user must be allowed on that branch (their own branch, or any
    branch of the organization they own). This is what lets a CEO with several
    branches sync from a till: get_shop_for_user() alone has no single branch
    to return for them and refuses (G3 in the architecture analysis).

    No header (web, Android, tills older than this): exactly the old behaviour.
    A header naming a device the cloud has never seen also falls back to the old
    behaviour, so a till whose pairing row is missing keeps syncing as before.
    """
    raw = (request.headers.get(DEVICE_HEADER) or '').strip()
    if not raw:
        return get_shop_for_user(request.user), None
    try:
        device_id = uuid.UUID(raw)
    except ValueError:
        raise ValidationError({DEVICE_HEADER: 'Must be a UUID.'})
    device = Device.objects.select_related('shop').filter(id=device_id).first()
    if device is None:
        return get_shop_for_user(request.user), None
    if not user_may_use_branch_device(request.user, device.shop):
        raise PermissionDenied('This device belongs to a branch your account cannot use.')
    if device.is_deleted and device.device_type == 'desktop':
        raise _DeviceRemoved()
    return device.shop, device


def payload_hash(entity_type, entity_id, operation, payload):
    """Stable fingerprint of what an operation SAYS (not when it was sent)."""
    body = json.dumps(
        {'entity_type': entity_type, 'entity_id': str(entity_id), 'operation': operation, 'payload': payload},
        sort_keys=True, separators=(',', ':'), default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


_REUSED_ID_ERROR = 'This operation id was already used for different content; it was not applied.'


def _replay_result(shop, op_id, op_hash):
    """Outcome for an op id this shop has already recorded, or None if it hasn't.

    Scoped to the shop on purpose: operation ids are client-generated UUIDs,
    and one shop must never learn (or be told "already applied") about another
    shop's operation just because the ids collided.

    Same content -> already_applied (a retry after a lost response). Different
    content -> rejected, so the till shows it as failed instead of believing a
    change reached the cloud when it never did (G6). Rows recorded before
    payload_hash existed have no hash to compare and are trusted as before."""
    stored = SyncOperation.objects.filter(id=op_id, shop=shop).values_list('payload_hash', flat=True).first()
    if stored is None:
        return None
    if stored and stored != op_hash:
        return {'id': op_id, 'status': 'rejected', 'error': _REUSED_ID_ERROR}
    return {'id': op_id, 'status': 'already_applied'}


# How far ahead of the server's clock a till's sale timestamp may be before
# we stop trusting it. A till with a wrong clock must not be able to push
# sales into the future (it would put them in tomorrow's reports).
MAX_CLIENT_CLOCK_AHEAD = timedelta(minutes=5)


def _sale_date_from_payload(payload):
    """The moment the sale was actually rung up, as recorded by the till.
    None -> caller leaves the model default (arrival time)."""
    raw = payload.get('date')
    if not isinstance(raw, str) or not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    if parsed > timezone.now() + MAX_CLIENT_CLOCK_AHEAD:
        return None
    return parsed


class SyncPushView(APIView):
    """POST /api/sync/push/

    Body:  {"operations": [{id, entity_type, entity_id, operation,
             payload, client_timestamp}, ...]} — exactly what
             desktop/src/main/sync.js already sends.
    Reply: {"results": [{"id": <op id>, "status": "applied" |
             "already_applied" | "rejected", "error"?: str}, ...]}

    Idempotent per operation id (see SyncOperation) — replaying the same
    batch after a dropped response is safe and returns `already_applied`
    instead of double-applying anything.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shop, device = resolve_sync_scope(request)
        operations = request.data.get('operations') or []
        results = []
        events = {}  # event name -> ids changed, broadcast once per name after the loop

        for op in operations:
            op_id = op.get('id')
            entity_type = op.get('entity_type')
            entity_id = op.get('entity_id')
            operation = op.get('operation')
            payload = op.get('payload') or {}

            if not op_id or not entity_id:
                continue  # malformed — nothing to key on, skip rather than crash the whole batch

            op_hash = payload_hash(entity_type, entity_id, operation, payload)
            replay = _replay_result(shop, op_id, op_hash)
            if replay is not None:
                results.append(replay)
                continue

            handler = _HANDLERS.get(entity_type)
            if handler is None:
                results.append({
                    'id': op_id, 'status': 'rejected',
                    'error': f'unknown entity_type "{entity_type}"',
                })
                continue

            if entity_type in _OWNER_ONLY_ENTITIES and _is_repeat_create(
                _REPEAT_MODELS[entity_type], shop, entity_id, operation,
            ):
                # A till re-sending a product/batch the cloud already has (any role): nothing to
                # apply, and nothing to refuse either. Rejecting it left sellers' tills with a
                # pile of permanent "failed" rows for products they never created.
                results.append({'id': op_id, 'status': 'already_applied'})
                continue

            if entity_type in _OWNER_ONLY_ENTITIES and not is_owner(request.user):
                results.append({
                    'id': op_id, 'status': 'rejected',
                    'error': 'Only the shop owner can add or change products and stock.',
                })
                continue

            # A seller's till can build this operation locally (see
            # desktop/src/main/main.js's pos:deleteSale, which already
            # blocks it client-side too) — this is the actual security
            # boundary, since anyone could otherwise replay a raw
            # /api/sync/push/ call bypassing a UI check entirely. Mirrors
            # sales.views.SaleViewSet.get_permissions' 'delete_sale' gate
            # for the web app's own DELETE /sales/{id}/.
            if entity_type == 'sale' and operation == 'delete' and not has_capability(request.user, 'delete_sale'):
                results.append({
                    'id': op_id, 'status': 'rejected',
                    'error': "Your role can't delete sales — ask an owner or manager.",
                })
                continue

            try:
                with transaction.atomic():
                    # CLAIM FIRST. The SyncOperation primary key is the lock:
                    # if two requests carry the same operation (a retry that
                    # overlaps the original, a double-sent batch), the second
                    # INSERT waits for the first to finish and then fails here
                    # -- instead of both running the handler and one of them
                    # being reported as a bogus "rejected" at the very end.
                    # Anything that goes wrong afterwards (including _Skip)
                    # rolls the claim back together with the handler's writes.
                    try:
                        SyncOperation.objects.create(
                            id=op_id, shop=shop, entity_type=entity_type,
                            entity_id=entity_id, operation=operation,
                            device=device, user=request.user, payload_hash=op_hash,
                        )
                    except IntegrityError:
                        raise _Duplicate()
                    handler(shop, entity_id, operation, payload, {'device': device, 'user': request.user})
            except _Duplicate:
                replay = _replay_result(shop, op_id, op_hash)
                if replay is not None:
                    results.append(replay)
                else:
                    results.append({
                        'id': op_id, 'status': 'rejected',
                        'error': 'This operation id is already in use by another shop.',
                    })
                continue
            except _Skip:
                continue  # leave it out of `results` — sync.js retries it next tick
            except Exception as exc:
                results.append({'id': op_id, 'status': 'rejected', 'error': str(exc)})
                continue

            if entity_type == 'order_doc':
                # The web app refreshes sales on sale.created; restaurant screens listen for the order.
                closed = Order.objects.filter(id=entity_id).only('sale_id').first()
                if closed is not None and closed.sale_id:
                    events.setdefault('sale.created', [])
                    if str(closed.sale_id) not in events['sale.created']:
                        events['sale.created'].append(str(closed.sale_id))
                event, changed_id = 'restaurant.order_closed', str(entity_id)
            elif entity_type == 'menu_availability':
                event, changed_id = 'restaurant.availability', str(payload.get('menu_item_id'))
            elif entity_type in ('sale_payment', 'sale_return'):
                event, changed_id = 'sale.updated', str(payload.get('sale_id'))
            else:
                event = f'{_EVENT_ENTITY.get(entity_type, entity_type)}.{_EVENT_VERB.get(operation, operation)}'
                changed_id = str(entity_id)
            events.setdefault(event, [])
            if changed_id not in events[event]:
                events[event].append(changed_id)
            results.append({'id': op_id, 'status': 'applied'})

        # ONE realtime event per kind of change in this batch, not one per operation (G9): 500
        # queued sales used to mean ~1,500 Shop-row locks, event rows and socket messages, each
        # making every other till pull again. Same event names as before (the web app reacts to
        # the name, then refetches), so every client keeps working; `ids` lists what changed.
        for event, ids in events.items():
            broadcast(shop, event, {'id': ids[0], 'ids': ids[:200], 'count': len(ids)})
        if device is not None and any(r['status'] in ('applied', 'already_applied') for r in results):
            Device.objects.filter(id=device.id).update(last_sync_at=timezone.now())

        return Response({'results': results})


def _serialize_product(o):
    return {
        'id': str(o.id), 'name': o.name, 'short_code': o.short_code, 'barcode': o.barcode,
        'category': o.category, 'unit': o.unit,
        'sell_price': str(o.sell_price), 'min_stock': o.min_stock, 'sold_by_weight': o.sold_by_weight,
        # Always included (even as null) -- this is a full snapshot pull,
        # not a partial patch, so a product whose image was removed (or
        # never had one) correctly clears/stays clear on every pulling
        # device. cloudinary_public_id is deliberately NOT included here:
        # it's internal bookkeeping the desktop/mobile never need to see
        # or manage -- image changes only ever happen through the
        # dedicated REST upload endpoint, which already has it server-side.
        'image_url': o.image_url,
    }


def _serialize_stock_batch(o):
    return {
        'id': str(o.id), 'product_id': str(o.item_id),
        'batch_number': o.batch_number,
        'quantity_received': o.quantity_received,
        'quantity_remaining': o.quantity_remaining,
        'cost_price': str(o.cost_price), 'selling_price': str(o.selling_price),
        'expiry_date': o.expiry_date.isoformat() if o.expiry_date else None,
    }


# Movements a till applies to its own stock when it pulls them. Sales, reversals, receipts and
# opening balances are left out: the till already derives those from the sales and batches it
# pulls, so applying them again would count them twice.
TILL_APPLIED_MOVEMENT_KINDS = (
    StockMovement.DAMAGE, StockMovement.ADJUSTMENT, StockMovement.REPAIR_PART,
    StockMovement.REPAIR_PART_RETURN, StockMovement.RETURN, StockMovement.TRANSFER,
)


def _serialize_stock_movement(o):
    return {
        'id': str(o.id), 'kind': o.kind, 'product_id': str(o.item_id),
        'variant_id': str(o.variant_id) if o.variant_id else None,
        'batch_id': str(o.batch_id) if o.batch_id else None,
        'quantity': str(o.quantity), 'reason': o.reason, 'actor_name': o.actor_name,
        'system_quantity': str(o.system_quantity) if o.system_quantity is not None else None,
        'counted_quantity': str(o.counted_quantity) if o.counted_quantity is not None else None,
        'client_ref': str(o.client_ref) if o.client_ref else None,
        'source': o.source, 'created_at': o.created_at.isoformat(),
    }


def _serialize_promotion(o):
    return _promotion_as_dict(o)


def _serialize_customer(o):
    return {
        'id': str(o.id), 'name': o.name, 'phone': o.phone,
        'email': o.email, 'address': o.address, 'notes': o.notes,
    }


def _serialize_sale(o):
    return {
        'id': str(o.id), 'customer_id': str(o.customer_id) if o.customer_id else None,
        'customer_name': o.customer_name, 'staff_name': o.staff_name,
        # Who rang it up, so a till can show sellers only their own sales and print per-seller reports.
        'seller_username': o.worker.user.username if o.worker_id and o.worker.user_id else None,
        'payment_method': o.payment_method, 'status': o.status,
        'amount_paid': str(o.amount_paid), 'invoice_number': o.invoice_number,
        'receipt_no': o.receipt_no or None, 'date': o.date.isoformat(),
        # Every payment, so a till sees installments taken elsewhere. Uses the prefetch in SyncPullView.
        'payments': [serialize_payment(p) for p in o.payments.all() if not p.is_deleted],
    }


def _serialize_sale_item(o):
    return {
        'id': str(o.id), 'sale_id': str(o.sale_id), 'product_id': str(o.item_id) if o.item_id else None,
        'item_name': o.item_name, 'category': o.category, 'quantity': o.quantity,
        'unit_price': str(o.unit_price), 'unit_cost': str(o.unit_cost), 'discount': str(o.discount),
        'stock_shortfall': o.stock_shortfall,
    }


# How far back each pull's NEXT cursor reaches behind the moment this pull
# started reading. `updated_at` is stamped when a row is saved, but the row
# only becomes visible when its transaction COMMITS, which can be later. A
# cursor set to "now" therefore skipped, forever, any row saved before the
# query but committed after it (G4). Re-reading the last couple of minutes on
# every pull closes that window; it is harmless because every applyPulled*
# handler on the till is idempotent, and the till skips rows it already applied
# at the same updated_at.
PULL_CURSOR_OVERLAP = timedelta(minutes=2)


def _parse_pull_cursor(raw, entity_types):
    """'<type index>|<updated_at iso>|<id>' -> (index, updated_at|None, id|None). Bad input = start."""
    if not raw:
        return 0, None, None
    try:
        index_s, at_s, id_s = raw.split('|', 2)
        index = int(index_s)
    except ValueError:
        return 0, None, None
    if not 0 <= index < max(len(entity_types), 1):
        return 0, None, None
    if not at_s:
        return index, None, None
    at = parse_datetime(at_s)
    try:
        row_id = uuid.UUID(id_s)
    except ValueError:
        return index, None, None
    return (index, at, row_id) if at else (index, None, None)


class SyncPullView(APIView):
    """GET /api/sync/pull/?since=<ISO8601>&entity_types=product,stock_batch

    Delta pull for rows changed in the cloud after `since` (everything, if
    omitted). Includes sales/sale_items now too — a sale rung up by the
    CEO on the web, or by another desktop, needs to reach THIS desktop so
    its local stock figures stay honest (see sync.js's applyPulledSale for
    how the desktop avoids double-deducting a sale it created itself, and
    how it deducts local stock for one it didn't).
    """
    permission_classes = [IsAuthenticated]
    serializers = {
        'product': (InventoryItem, _serialize_product),
        'stock_batch': (StockBatch, _serialize_stock_batch),
        'customer': (Customer, _serialize_customer),
        'sale': (Sale, _serialize_sale),
        'sale_item': (SaleItem, _serialize_sale_item),
        # After stock_batch, so a till always has the batch before a movement that names it.
        'stock_movement': (StockMovement, _serialize_stock_movement),
        'promotion': (Promotion, _serialize_promotion),
    }

    def get(self, request):
        shop, _device = resolve_sync_scope(request)
        # Taken BEFORE reading anything: next_since is measured from the
        # moment our snapshot could start missing late commits.
        read_started = timezone.now()
        since_param = request.query_params.get('since')
        since = parse_datetime(since_param) if since_param else None
        requested = request.query_params.get('entity_types')
        # Restaurant configuration is opt-in: only when named AND the branch is a restaurant branch.
        available = {**self.serializers, **restaurant_sync.allowed_for(shop)} if requested else self.serializers
        entity_types = [t for t in (requested.split(',') if requested else list(self.serializers)) if t in available]

        # Paging (G11). Without `limit` (older tills) everything changed since `since` comes back
        # in one response, exactly as before. With `limit`, rows come back in a stable order
        # (entity types in parent-before-child order, then updated_at, then id) and `next_cursor`
        # says where the next page starts; the till keeps asking until has_more is false.
        try:
            limit = max(1, min(int(request.query_params.get('limit')), 2000)) if request.query_params.get('limit') else None
        except ValueError:
            limit = None
        start_type, cursor_at, cursor_id = _parse_pull_cursor(request.query_params.get('cursor'), entity_types)

        operations = []
        has_more = False
        next_cursor = None
        for index, entity_type in enumerate(entity_types):
            if index < start_type:
                continue
            model, serialize = available[entity_type]
            qs = model.objects.filter(shop=shop)
            if model is Sale:
                qs = qs.prefetch_related('payments').select_related('worker__user')
            if model is StockMovement:
                # Units moved IN from another branch reach tills as new batches; only moves OUT apply.
                qs = qs.filter(kind__in=TILL_APPLIED_MOVEMENT_KINDS).exclude(kind=StockMovement.TRANSFER, quantity__gt=0)
            if since:
                qs = qs.filter(updated_at__gt=since)
            if index == start_type and cursor_at is not None:
                qs = qs.filter(Q(updated_at__gt=cursor_at) | Q(updated_at=cursor_at, id__gt=cursor_id))
            qs = qs.order_by('updated_at', 'id')
            if limit is not None:
                room = limit - len(operations)
                rows = list(qs[:room + 1])
                if len(rows) > room:
                    rows = rows[:room]
                    has_more = True
            else:
                rows = qs
            for obj in rows:
                operations.append({
                    'entity_type': entity_type,
                    'entity_id': str(obj.id),
                    'operation': 'delete' if obj.is_deleted else 'update',
                    'payload': serialize(obj),
                    'updated_at': obj.updated_at.isoformat(),
                })
            if has_more:
                last = rows[-1] if rows else None
                next_cursor = f'{index}|{last.updated_at.isoformat()}|{last.id}' if last else f'{index}||'
                break
            if limit is not None and len(operations) >= limit and index + 1 < len(entity_types):
                # This page is full exactly at a type boundary: the next page starts at the next type.
                has_more = True
                next_cursor = f'{index + 1}||'
                break

        return Response({
            'has_more': has_more,
            'next_cursor': next_cursor,
            'operations': operations,
            'server_time': timezone.now().isoformat(),
            # The cursor to send as `since` next time (see PULL_CURSOR_OVERLAP).
            # server_time stays exactly as before for older tills and displays.
            'next_since': (read_started - PULL_CURSOR_OVERLAP).isoformat(),
            'sync_protocol': SYNC_PROTOCOL,
            # Which modules the branch uses now (retail / restaurant / service). Tills save these at
            # sign-in; an owner can switch one on or off later (announced live as
            # branch.modules_changed), so the next pull keeps the till current. Older tills ignore it.
            'modules': sorted(branch_modules(shop)),
        })


class SyncReconcileView(APIView):
    """GET /api/sync/reconcile/ — a cheap trust-but-verify check the
    desktop runs periodically (and right after reconnecting) alongside
    its normal incremental pull/push loop, per-entity-type row counts
    only, no payloads. This is the answer to "once it detects internet,
    make sure the desktop actually has the same data as the backend":
    the ordinary delta pull (SyncPullView, `since=<cursor>`) is trusted to
    keep things in sync tick by tick, but if a row was ever missed —
    applied out of order and silently skipped, a local DB file that was
    manually copied from another machine, anything — a cursor-based pull
    would never notice, since it only asks "what changed since X" and a
    row it already thinks it has never comes back. Comparing plain counts
    catches that drift; the desktop's reaction (see sync.js's
    reconcileTick) is simply to drop its pull cursor back to "since the
    beginning of time" and let SyncPullView's existing full-snapshot mode
    (no `since` param) resend everything — no separate repair codepath to
    maintain, just the same pull logic running once with a wider net.
    """
    permission_classes = [IsAuthenticated]
    models = {
        'product': InventoryItem,
        'stock_batch': StockBatch,
        'customer': Customer,
        'sale': Sale,
        'sale_item': SaleItem,
    }

    def get(self, request):
        shop, _device = resolve_sync_scope(request)
        counts = {}
        for entity_type, model in self.models.items():
            qs = model.objects.filter(shop=shop)
            counts[entity_type] = {
                'active': qs.filter(is_deleted=False).count(),
                'total': qs.count(),  # includes soft-deleted tombstones, which the desktop also keeps (see db.js)
            }
        return Response({'counts': counts, 'server_time': timezone.now().isoformat()})
