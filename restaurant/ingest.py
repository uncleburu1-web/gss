"""Finished restaurant orders arriving from the hub (sync protocol 6).

Design: docs/restaurant/OFFLINE_SYNCHRONIZATION.md §4 ("Up: orders"), API_SPECIFICATION.md (order_doc),
ADR-023. Called from sync/views.py inside the push's per-operation transaction, so an order is
stored completely or not at all, and a resend of the same operation is answered `already_applied`
before it gets here.

What is checked before anything is written:
  * the branch uses the restaurant, and the sender is the branch's hub (one writer: no second copy
    of an order can exist to conflict with);
  * every menu item, option, table, station and tax rule the document names belongs to this branch;
  * the bill adds up: it is worked out again with the shared bill engine from the document's OWN
    frozen copies (item prices, modifiers, discount as entered, service charge and tax rules as they
    were when the order opened). Never from today's settings, so a rate changed after the bill was
    printed cannot reject a valid order;
  * a closed order was paid in full.

Then the order, items, tickets, bill, charges and payments are stored with the hub's own ids, and a
closed order also becomes a `Sale` so existing reports include restaurant takings (ADR-023). That
Sale names no stock item and allocates no stock: restaurant sales never move stock (DOMAIN_MODEL §1).
"""
import uuid
from datetime import timezone as dt_timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.modules import has_module
from sales.models import Sale, SaleItem, SalePayment
from staff.models import Worker

from . import billing
from .models import (
    Bill, BillCharge, BillPayment, KitchenStation, KitchenTicket, KitchenTicketItem, MenuAvailability, MenuItem,
    ModifierOption, Order, OrderItem, OrderItemModifier, RestaurantSettings, RestaurantTable, TaxRule,
)

ORDER_PROTOCOL = 6
CENT = Decimal('0.01')
SALE_NAMESPACE = uuid.UUID('6f1c1d1e-6a52-4b8e-9a2f-2d1b7a4e0c11')
SERVICE_LABELS = {'dine_in': 'Dine-in', 'takeaway': 'Takeaway', 'counter': 'Counter'}

# Audit actions the hub records (gssdesktop restaurant/engine.js, printing.js, screens.js). Accepted
# only from the hub of a restaurant branch, from protocol 6.
AUDIT_ACTIONS = {
    'restaurant.item_voided': 'Voided an item on an order',
    'restaurant.table_moved': 'Moved an order to another table',
    'restaurant.discount': 'Changed the discount on a bill',
    'restaurant.payment_override': 'Took payment before everything was served',
    'restaurant.order_cancelled': 'Cancelled an order',
    'restaurant.sold_out': 'Switched an item sold out / back on',
    'restaurant.ticket_reprinted': 'Reprinted a kitchen ticket',
    'restaurant.screen_paired': 'Added a kitchen screen',
    'restaurant.screen_removed': 'Removed a kitchen screen',
}


class Rejected(ValueError):
    """An operation the cloud refuses. The till shows `<code>: <message>` as a failed upload."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


def require_hub(shop, ctx):
    if not has_module(shop, 'restaurant'):
        raise Rejected('module_disabled', 'This branch does not use the restaurant.')
    device = (ctx or {}).get('device')
    settings = RestaurantSettings.objects.filter(shop=shop, is_deleted=False).first()
    hub_id = settings.hub_device_id if settings else None
    if device is None or hub_id is None or device.id != hub_id:
        raise Rejected('not_the_hub', 'Only the branch\'s restaurant hub can send restaurant orders.')


# ---------------------------------------------------------------------------------------------
# Small parsers: the document comes from a device, so nothing is trusted to be well formed.
# ---------------------------------------------------------------------------------------------
def _uuid(value, what):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise Rejected('bad_document', f'{what} is not a valid id.')


def _opt_uuid(value, what):
    return None if value in (None, '') else _uuid(value, what)


def _money(value, what):
    try:
        d = Decimal(str(value))
    except Exception:
        raise Rejected('bad_document', f'{what} is not an amount.')
    if not d.is_finite():
        raise Rejected('bad_document', f'{what} is not an amount.')
    return d.quantize(CENT)


def _qty(value, what):
    try:
        d = Decimal(str(value))
    except Exception:
        raise Rejected('bad_document', f'{what} is not a quantity.')
    if not d.is_finite() or d < 0:
        raise Rejected('bad_document', f'{what} is not a quantity.')
    return d


def _dt(value, what, required=True):
    if value in (None, ''):
        if required:
            raise Rejected('bad_document', f'{what} is missing.')
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise Rejected('bad_document', f'{what} is not a date and time.')
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=dt_timezone.utc)


def _text(value, limit):
    return str(value or '')[:limit]


def _owned(model, shop, ids, code, what):
    """Every id must be a row of this branch (deleted rows still count: they existed when ordered)."""
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    found = {o.id: o for o in model.objects.filter(shop=shop, id__in=ids)}
    if len(found) != len(ids):
        raise Rejected(code, f'The order names a {what} that is not in this branch.')
    return found


# ---------------------------------------------------------------------------------------------
# The bill, worked out again from the document alone
# ---------------------------------------------------------------------------------------------
def _bill_input(doc):
    pricing = doc.get('pricing') or {}
    service = pricing.get('service_charge') or {}
    return {
        'service_type': doc.get('service_type'),
        'business_date': doc.get('business_date'),
        'lines': [{'id': str(i.get('id')), 'unit_price': str(i.get('unit_price')), 'modifiers_price': str(i.get('modifiers_price') or '0'),
                   'quantity': str(i.get('quantity')), 'voided_quantity': str(i.get('voided_quantity') or '0'),
                   'tax_class': i.get('tax_class') or ''} for i in doc.get('items') or []],
        'discount': (doc.get('bill') or {}).get('discount_spec') or None,
        'service_charge': {'rate': str(service.get('rate') or '0'), 'service_types': service.get('service_types') or []},
        'tax_rules': [{**r, 'rate': str(r.get('rate'))} for r in pricing.get('tax_rules') or []],
    }


def _same_money(a, b):
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return False


def check_bill(doc):
    """The document's bill must be exactly what its own frozen inputs give."""
    bill = doc.get('bill') or {}
    try:
        expected = billing.compute_bill(_bill_input(doc))
    except billing.BillError as exc:
        raise Rejected('bill_mismatch', f'The bill cannot be worked out from the order ({exc.code}).')
    except Exception:
        raise Rejected('bad_document', 'The order\'s prices or pricing are not readable.')
    for field in ('subtotal', 'discount', 'net', 'total'):
        if not _same_money(bill.get(field), expected[field]):
            raise Rejected('bill_mismatch', f'The bill {field} {bill.get(field)} does not add up (expected {expected[field]}).')
    got = bill.get('charges') or []
    if len(got) != len(expected['charges']):
        raise Rejected('bill_mismatch', 'The bill\'s service charge and tax lines do not add up.')
    for g, e in zip(got, expected['charges']):
        same = (g.get('kind') == e['kind'] and str(g.get('tax_rule_id') or '') == str(e['tax_rule_id'] or '')
                and g.get('name') == e['name'] and _same_money(g.get('rate'), e['rate']) and bool(g.get('inclusive')) == bool(e['inclusive'])
                and _same_money(g.get('base'), e['base']) and _same_money(g.get('amount'), e['amount']))
        if not same:
            raise Rejected('bill_mismatch', f'The bill line "{g.get("name")}" does not add up (expected {e["amount"]}).')
    return expected


# ---------------------------------------------------------------------------------------------
# order_doc
# ---------------------------------------------------------------------------------------------
def apply_order_doc(shop, entity_id, operation, payload, ctx=None):
    if operation != 'create':
        raise Rejected('bad_document', 'A finished order can only be created.')
    require_hub(shop, ctx)
    doc = payload or {}
    order_id = _uuid(doc.get('id'), 'The order')
    if str(order_id) != str(entity_id):
        raise Rejected('bad_document', 'The order id does not match.')
    existing = Order.objects.filter(id=order_id).first()
    if existing is not None:
        if existing.shop_id != shop.id:
            raise Rejected('bad_document', 'That order id belongs to another branch.')
        return existing  # already stored (sent again under a new operation id): nothing to do
    status = doc.get('status')
    if status not in ('closed', 'cancelled'):
        raise Rejected('order_open', 'Only closed or cancelled orders are sent.')
    if doc.get('service_type') not in ('dine_in', 'takeaway', 'counter'):
        raise Rejected('bad_document', 'Unknown service type.')
    business_date = parse_date(str(doc.get('business_date') or ''))
    if business_date is None:
        raise Rejected('bad_document', 'The business date is missing.')
    items = doc.get('items') or []
    tickets = doc.get('tickets') or []
    bill = doc.get('bill')
    pricing = doc.get('pricing') or {}
    if not isinstance(items, list) or not isinstance(tickets, list) or (bill is not None and not isinstance(bill, dict)):
        raise Rejected('bad_document', 'The order is not readable.')

    # Everything named must be this branch's.
    menu = _owned(MenuItem, shop, [_opt_uuid(i.get('menu_item_id'), 'A menu item') for i in items], 'unknown_menu_item', 'menu item')
    options = _owned(ModifierOption, shop, [_opt_uuid(m.get('option_id'), 'A choice') for i in items for m in (i.get('modifiers') or [])],
                     'unknown_option', 'choice')
    table = _owned(RestaurantTable, shop, [_opt_uuid(doc.get('table_id'), 'The table')], 'unknown_table', 'table')
    stations = _owned(KitchenStation, shop, [_opt_uuid(t.get('station_id'), 'A station') for t in tickets], 'unknown_station', 'station')
    rule_ids = [_opt_uuid(r.get('id'), 'A tax rule') for r in pricing.get('tax_rules') or []]
    rule_ids += [_opt_uuid(c.get('tax_rule_id'), 'A tax rule') for c in ((bill or {}).get('charges') or [])]
    rules = _owned(TaxRule, shop, rule_ids, 'unknown_tax_rule', 'tax rule')

    if status == 'closed':
        if not bill:
            raise Rejected('bill_not_settled', 'A closed order has no bill.')
        check_bill(doc)
        paid = sum((_money(p.get('amount'), 'A payment') for p in bill.get('payments') or []), Decimal('0'))
        if paid < _money(bill.get('total'), 'The total'):
            raise Rejected('bill_not_settled', f'The order was closed with {paid} paid of {bill.get("total")}.')
    elif bill:
        check_bill(doc)
        if any(_money(p.get('amount'), 'A payment') != 0 for p in bill.get('payments') or []):
            raise Rejected('bad_document', 'A cancelled order cannot have payments.')

    device = (ctx or {}).get('device')
    order = Order.objects.create(
        id=order_id, shop=shop, number=_text(doc.get('number'), 30), service_type=doc['service_type'],
        table=table.get(_opt_uuid(doc.get('table_id'), 'The table')), guests=doc.get('guests') if isinstance(doc.get('guests'), int) and doc['guests'] >= 0 else None,
        waiter=_text(doc.get('waiter'), 150), opened_by=_text(doc.get('opened_by'), 150), status=status, business_date=business_date,
        opened_at=_dt(doc.get('opened_at'), 'The opening time'), closed_at=_dt(doc.get('closed_at'), 'The closing time', required=False),
        cancel_reason=_text(doc.get('cancel_reason'), 255), notes=_text(doc.get('notes'), 2000), pricing=pricing, device=device,
    )
    by_id = {}
    for i in items:
        item = OrderItem.objects.create(
            id=_uuid(i.get('id'), 'An item'), shop=shop, order=order, menu_item=menu.get(_opt_uuid(i.get('menu_item_id'), 'A menu item')),
            name=_text(i.get('name'), 120), unit_price=_money(i.get('unit_price'), 'A price'),
            modifiers_price=_money(i.get('modifiers_price') or '0', 'A choice price'), tax_class=_text(i.get('tax_class'), 30),
            quantity=_qty(i.get('quantity'), 'A quantity'), voided_quantity=_qty(i.get('voided_quantity') or '0', 'A voided quantity'),
            state=_text(i.get('state'), 12), waste=bool(i.get('waste')), void_reason=_text(i.get('void_reason'), 255),
            notes=_text(i.get('notes'), 255), sent_at=_dt(i.get('sent_at'), 'A send time', required=False),
            served_at=_dt(i.get('served_at'), 'A serve time', required=False),
        )
        if item.voided_quantity > item.quantity:
            raise Rejected('bad_document', 'More was voided than ordered.')
        by_id[str(item.id)] = item
        for m in i.get('modifiers') or []:
            OrderItemModifier.objects.create(order_item=item, option=options.get(_opt_uuid(m.get('option_id'), 'A choice')),
                                             name=_text(m.get('name'), 120), price_delta=_money(m.get('price_delta') or '0', 'A choice price'))
    for t in tickets:
        ticket = KitchenTicket.objects.create(
            id=_uuid(t.get('id'), 'A ticket'), shop=shop, order=order, station=stations.get(_opt_uuid(t.get('station_id'), 'A station')),
            number=_text(t.get('number'), 30), kind=_text(t.get('kind'), 8), sent_at=_dt(t.get('sent_at'), 'A ticket time'),
            sent_by=_text(t.get('sent_by'), 150),
        )
        for ti in t.get('items') or []:
            target = by_id.get(str(ti.get('order_item_id')))
            if target is None:
                raise Rejected('bad_document', 'A ticket names an item that is not on the order.')
            KitchenTicketItem.objects.create(
                id=_uuid(ti.get('id'), 'A ticket item'), shop=shop, ticket=ticket, order_item=target,
                quantity=_qty(ti.get('quantity'), 'A ticket quantity'), voided_quantity=_qty(ti.get('voided_quantity') or '0', 'A ticket quantity'),
                state=_text(ti.get('state'), 12), started_at=_dt(ti.get('started_at'), 'A start time', required=False),
                ready_at=_dt(ti.get('ready_at'), 'A ready time', required=False), served_at=_dt(ti.get('served_at'), 'A serve time', required=False),
            )
    stored_bill = None
    if bill:
        stored_bill = Bill.objects.create(
            id=_uuid(bill.get('id'), 'The bill'), shop=shop, order=order, number=_text(bill.get('number'), 40), status=_text(bill.get('status'), 10),
            subtotal=_money(bill.get('subtotal'), 'The subtotal'), discount=_money(bill.get('discount') or '0', 'The discount'),
            discount_reason=_text(bill.get('discount_reason'), 255), net=_money(bill.get('net'), 'The net'), total=_money(bill.get('total'), 'The total'),
        )
        for n, c in enumerate(bill.get('charges') or []):
            BillCharge.objects.create(
                bill=stored_bill, kind=_text(c.get('kind'), 20), tax_rule=rules.get(_opt_uuid(c.get('tax_rule_id'), 'A tax rule')),
                name=_text(c.get('name'), 60), rate=Decimal(str(c.get('rate'))), inclusive=bool(c.get('inclusive')),
                base=_money(c.get('base'), 'A charge base'), amount=_money(c.get('amount'), 'A charge'), sort=n,
            )
        for p in bill.get('payments') or []:
            BillPayment.objects.create(
                id=_uuid(p.get('id'), 'A payment'), shop=shop, bill=stored_bill, method=_text(p.get('method'), 20),
                amount=_money(p.get('amount'), 'A payment'), tendered=None if p.get('tendered') in (None, '') else _money(p.get('tendered'), 'Cash tendered'),
                change=_money(p.get('change') or '0', 'Change'), tip=_money(p.get('tip') or '0', 'A tip'), reference=_text(p.get('reference'), 100),
                shift_ref=_opt_uuid(p.get('shift_ref'), 'A shift'), received_by=_text(p.get('received_by'), 150),
                received_at=_dt(p.get('received_at'), 'A payment time'),
            )
    if status == 'closed' and stored_bill is not None and stored_bill.total > 0:
        order.sale = _write_sale(shop, order, stored_bill, device)
        order.save(update_fields=['sale', 'updated_at'])
    return order


def _worker(shop, username):
    if not username:
        return None
    user = get_user_model().objects.filter(username=username).first()
    return Worker.objects.filter(user=user, shop=shop).first() if user else None


def _share(total_discount, amounts):
    """Split a discount across lines in proportion to their amounts, to the kobo, adding up exactly."""
    total = sum(amounts, Decimal('0'))
    if not total_discount or not total:
        return [Decimal('0.00')] * len(amounts)
    raw = [total_discount * a / total for a in amounts]
    shares = [r.quantize(CENT, rounding=ROUND_DOWN) for r in raw]
    left = int(((total_discount - sum(shares, Decimal('0'))) / CENT).to_integral_value())
    for i in sorted(range(len(raw)), key=lambda k: raw[k] - shares[k], reverse=True)[:left]:
        shares[i] += CENT
    return shares


def _write_sale(shop, order, bill, device):
    """The closed order as a Sale, so the existing reports, payment summaries and seller views count it.

    Lines: each item still served (net of voids) at its frozen price including choices, with its
    share of the discount; then the service charge; then each tax ADDED to the bill. Taxes already
    inside the prices are not added again. The lines add up to the bill total exactly. No stock item,
    no allocation: nothing here moves stock."""
    payments = list(bill.payments.all())
    main_method = max(payments, key=lambda p: p.amount).method if payments else 'cash'
    cashier = payments[-1].received_by if payments else ''
    sale = Sale.objects.create(
        id=uuid.uuid5(SALE_NAMESPACE, str(order.id)), shop=shop, status='completed', amount_paid=bill.total,
        receipt_no=f'{order.number} {order.business_date.isoformat()}'[:40],
        customer_name=(f'Table {order.table.label}' if order.table else SERVICE_LABELS.get(order.service_type, 'Walk-in'))[:150],
        staff_name=(order.waiter or cashier)[:150], worker=_worker(shop, cashier) or _worker(shop, order.waiter),
        payment_method=main_method if main_method in dict(Sale.PAYMENT_CHOICES) else 'cash',
        date=order.closed_at or timezone.now(), device=device, received_at=timezone.now(),
        shift_ref=next((p.shift_ref for p in reversed(payments) if p.shift_ref), None),
    )
    lines = []
    for item in order.items.all():
        left = item.quantity - item.voided_quantity
        if left <= 0:
            continue
        unit = item.unit_price + item.modifiers_price
        mods = [m.name for m in item.modifiers.all()]
        lines.append((item, left, unit, f'{item.name} ({", ".join(mods)})' if mods else item.name))
    shares = _share(bill.discount, [(unit * left).quantize(CENT, rounding=ROUND_HALF_UP) for _, left, unit, _ in lines])
    for (item, left, unit, name), share in zip(lines, shares):
        SaleItem.objects.create(shop=shop, sale=sale, item=None, item_name=name[:200], category='restaurant',
                                quantity=left, unit_price=unit, unit_cost=Decimal('0.00'), discount=share, tax_rate=Decimal('0.00'))
    for charge in bill.charges.all():
        if charge.kind == 'tax' and charge.inclusive:
            continue  # already inside the item prices
        SaleItem.objects.create(shop=shop, sale=sale, item=None, item_name=f'{charge.name} {charge.rate.normalize():f}%'[:200],
                                category='restaurant_tax' if charge.kind == 'tax' else 'restaurant_charge',
                                quantity=Decimal('1'), unit_price=charge.amount, unit_cost=Decimal('0.00'), tax_rate=Decimal('0.00'))
    for p in payments:
        SalePayment.objects.create(
            id=uuid.uuid5(SALE_NAMESPACE, f'payment:{p.id}'), shop=shop, sale=sale,
            method=p.method if p.method in dict(SalePayment.METHOD_CHOICES) else 'other',
            amount=p.amount, tendered=p.tendered, change_given=p.change, reference=p.reference, paid_at=p.received_at,
            source='till', shift_ref=p.shift_ref, device=device,
        )
    total = sum((i.total for i in sale.items.all()), Decimal('0'))
    if total != bill.total:  # cannot happen when check_bill passed; refuse rather than store a wrong sale
        raise Rejected('bill_mismatch', f'The sale lines add up to {total}, not {bill.total}.')
    return sale


# ---------------------------------------------------------------------------------------------
# menu_availability
# ---------------------------------------------------------------------------------------------
def apply_menu_availability(shop, entity_id, operation, payload, ctx=None):
    require_hub(shop, ctx)
    p = payload or {}
    item = _owned(MenuItem, shop, [_uuid(p.get('menu_item_id'), 'The menu item')], 'unknown_menu_item', 'menu item')
    menu_item = next(iter(item.values()))
    day = parse_date(str(p.get('business_date') or ''))
    if day is None:
        raise Rejected('bad_document', 'The business date is missing.')
    at = _dt(p.get('at'), 'The time')
    row = MenuAvailability.objects.filter(menu_item=menu_item, business_date=day).first()
    if row is None:
        MenuAvailability.objects.create(shop=shop, menu_item=menu_item, business_date=day, sold_out=bool(p.get('sold_out')),
                                        by=_text(p.get('by'), 150), at=at)
    elif at >= row.at:  # the latest switch of the day wins, whatever order the uploads arrive in
        row.sold_out, row.by, row.at = bool(p.get('sold_out')), _text(p.get('by'), 150), at
        row.save(update_fields=['sold_out', 'by', 'at'])
