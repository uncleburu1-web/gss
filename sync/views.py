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
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.permissions import is_owner
from core.capabilities import has_capability
from core.utils import get_shop_for_user
from customers.models import Customer
from inventory.models import InventoryItem, StockBatch
from realtime.events import broadcast
from sales.models import Sale, SaleItem, next_invoice_number
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

def _apply_product(shop, entity_id, operation, payload):
    if operation == 'delete':
        InventoryItem.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return
    defaults = {
        'name': payload.get('name', ''),
        'short_code': payload.get('short_code') or '',
        'barcode': payload.get('barcode') or None,
        'category': payload.get('category') or 'other',
        'unit': payload.get('unit') or 'PIECE',
        'sell_price': payload.get('sell_price') or 0,
        'min_stock': payload.get('min_stock') if payload.get('min_stock') is not None else 2,
    }
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
    InventoryItem.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)


def _apply_stock_batch(shop, entity_id, operation, payload):
    if operation == 'delete':
        StockBatch.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return
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
        'quantity_received': payload.get('quantity_received') or 0,
        'quantity_remaining': payload.get('quantity_remaining') or 0,
        'cost_price': payload.get('cost_price') or 0,
        'selling_price': selling_price,
        'expiry_date': payload.get('expiry_date') or None,
    }
    StockBatch.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)


def _apply_customer(shop, entity_id, operation, payload):
    if operation == 'delete':
        Customer.objects.filter(id=entity_id, shop=shop).update(is_deleted=True)
        return
    defaults = {
        'name': payload.get('name', ''),
        'phone': payload.get('phone') or '',
        'email': payload.get('email') or '',
        'address': payload.get('address') or '',
        'notes': payload.get('notes') or '',
    }
    Customer.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)


def _apply_sale(shop, entity_id, operation, payload):
    if operation == 'delete':
        try:
            sale = Sale.objects.get(id=entity_id, shop=shop)
        except Sale.DoesNotExist:
            return  # never arrived and now it's deleted too — nothing to do
        for sale_item in sale.items.all():
            _restore_stock(sale_item)
            sale_item.is_deleted = True
            sale_item.save(update_fields=['is_deleted', 'updated_at'])
        sale.is_deleted = True
        sale.save(update_fields=['is_deleted', 'updated_at'])
        return

    defaults = {
        'customer_name': payload.get('customer_name') or 'Walk-in',
        'staff_name': payload.get('staff_name') or '',
        'payment_method': payload.get('payment_method') or 'cash',
        'status': payload.get('status') or 'completed',
        'amount_paid': payload.get('amount_paid') or 0,
    }
    customer_id = payload.get('customer_id')
    if customer_id:
        customer = Customer.objects.filter(id=customer_id, shop=shop).first()
        if customer is None:
            raise _Skip()  # this sale's customer hasn't synced yet
        defaults['customer'] = customer
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
    Sale.objects.update_or_create(id=entity_id, shop=shop, defaults=defaults)


def _apply_sale_item(shop, entity_id, operation, payload):
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
        'quantity': payload.get('quantity') or 1,
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
        _allocate_stock(sale_item, shop)

    if sale.status == 'completed':
        sale.amount_paid = sale.total
        sale.save(update_fields=['amount_paid'])


_HANDLERS = {
    'product': _apply_product,
    'stock_batch': _apply_stock_batch,
    'customer': _apply_customer,
    'sale': _apply_sale,
    'sale_item': _apply_sale_item,
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
        shop = get_shop_for_user(request.user)
        operations = request.data.get('operations') or []
        results = []

        for op in operations:
            op_id = op.get('id')
            entity_type = op.get('entity_type')
            entity_id = op.get('entity_id')
            operation = op.get('operation')
            payload = op.get('payload') or {}

            if not op_id or not entity_id:
                continue  # malformed — nothing to key on, skip rather than crash the whole batch

            if SyncOperation.objects.filter(id=op_id).exists():
                results.append({'id': op_id, 'status': 'already_applied'})
                continue

            handler = _HANDLERS.get(entity_type)
            if handler is None:
                results.append({
                    'id': op_id, 'status': 'rejected',
                    'error': f'unknown entity_type "{entity_type}"',
                })
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
                    handler(shop, entity_id, operation, payload)
                    SyncOperation.objects.create(
                        id=op_id, shop=shop, entity_type=entity_type,
                        entity_id=entity_id, operation=operation,
                    )
            except _Skip:
                continue  # leave it out of `results` — sync.js retries it next tick
            except Exception as exc:
                results.append({'id': op_id, 'status': 'rejected', 'error': str(exc)})
                continue

            broadcast(
                shop,
                f'{_EVENT_ENTITY.get(entity_type, entity_type)}.{_EVENT_VERB.get(operation, operation)}',
                {'id': entity_id},
            )
            results.append({'id': op_id, 'status': 'applied'})

        return Response({'results': results})


def _serialize_product(o):
    return {
        'id': str(o.id), 'name': o.name, 'short_code': o.short_code, 'barcode': o.barcode,
        'category': o.category, 'unit': o.unit,
        'sell_price': str(o.sell_price), 'min_stock': o.min_stock,
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


def _serialize_customer(o):
    return {
        'id': str(o.id), 'name': o.name, 'phone': o.phone,
        'email': o.email, 'address': o.address, 'notes': o.notes,
    }


def _serialize_sale(o):
    return {
        'id': str(o.id), 'customer_id': str(o.customer_id) if o.customer_id else None,
        'customer_name': o.customer_name, 'staff_name': o.staff_name,
        'payment_method': o.payment_method, 'status': o.status,
        'amount_paid': str(o.amount_paid), 'invoice_number': o.invoice_number,
        'date': o.date.isoformat(),
    }


def _serialize_sale_item(o):
    return {
        'id': str(o.id), 'sale_id': str(o.sale_id), 'product_id': str(o.item_id) if o.item_id else None,
        'item_name': o.item_name, 'category': o.category, 'quantity': o.quantity,
        'unit_price': str(o.unit_price), 'unit_cost': str(o.unit_cost), 'discount': str(o.discount),
        'stock_shortfall': o.stock_shortfall,
    }


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
    }

    def get(self, request):
        shop = get_shop_for_user(request.user)
        since_param = request.query_params.get('since')
        since = parse_datetime(since_param) if since_param else None
        requested = request.query_params.get('entity_types')
        entity_types = requested.split(',') if requested else list(self.serializers)

        operations = []
        for entity_type in entity_types:
            spec = self.serializers.get(entity_type)
            if spec is None:
                continue
            model, serialize = spec
            qs = model.objects.filter(shop=shop)
            if since:
                qs = qs.filter(updated_at__gt=since)
            for obj in qs:
                operations.append({
                    'entity_type': entity_type,
                    'entity_id': str(obj.id),
                    'operation': 'delete' if obj.is_deleted else 'update',
                    'payload': serialize(obj),
                    'updated_at': obj.updated_at.isoformat(),
                })

        return Response({'operations': operations, 'server_time': timezone.now().isoformat()})


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
        shop = get_shop_for_user(request.user)
        counts = {}
        for entity_type, model in self.models.items():
            qs = model.objects.filter(shop=shop)
            counts[entity_type] = {
                'active': qs.filter(is_deleted=False).count(),
                'total': qs.count(),  # includes soft-deleted tombstones, which the desktop also keeps (see db.js)
            }
        return Response({'counts': counts, 'server_time': timezone.now().isoformat()})
