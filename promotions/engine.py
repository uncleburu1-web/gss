"""The promotion evaluator. MUST stay identical to promotions.js on the till and in the web/Android
apps (both are tested against the same cases in promotions/tests.py and test/promotions.test.js).

    best_discounts(lines, promotions, now) -> [{'discount': Decimal, 'promotion_id': str|None}, ...]

`lines`: [{'product_id', 'category', 'quantity', 'unit_price'}]; `promotions`: dicts with the
Promotion fields (id, kind, value, applies_to, item_id, category, min_quantity, buy_quantity,
get_quantity, starts_at, ends_at, is_active). Money rounds half-up to kobo.
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


def _dec(v):
    return Decimal(str(v or 0))


def _when(v):
    if v is None or isinstance(v, datetime):
        return v
    from django.utils.dateparse import parse_datetime
    return parse_datetime(str(v))


def is_live(promo, now):
    if not promo.get('is_active', True) or promo.get('is_deleted'):
        return False
    starts, ends = _when(promo.get('starts_at')), _when(promo.get('ends_at'))
    return not ((starts and now < starts) or (ends and now >= ends))


def matches(promo, line):
    scope = promo.get('applies_to')
    if scope == 'all':
        return True
    if scope == 'category':
        return bool(promo.get('category')) and promo.get('category') == line.get('category')
    return bool(line.get('product_id')) and str(promo.get('item_id')) == str(line.get('product_id'))


def discount_for(promo, line):
    # Quantities may be kg (3 decimals) for weighed products: % off and amount-off-each (per kg)
    # apply to the exact weight; buy-X-get-Y counts whole units only.
    qty = _dec(line.get('quantity')).quantize(Decimal('0.001'))
    price = _dec(line.get('unit_price'))
    if qty <= 0 or price <= 0:
        return ZERO
    gross = price * qty
    kind = promo.get('kind')
    if kind == 'buy_x_get_y':
        buy, get = int(promo.get('buy_quantity') or 0), int(promo.get('get_quantity') or 0)
        if buy <= 0 or get <= 0:
            return ZERO
        free = (int(qty) // (buy + get)) * get
        return min(gross, price * free).quantize(CENT, ROUND_HALF_UP)
    if qty < int(promo.get('min_quantity') or 1):
        return ZERO
    value = _dec(promo.get('value'))
    if kind == 'percent_off':
        pct = min(max(value, ZERO), Decimal('100'))
        return (gross * pct / 100).quantize(CENT, ROUND_HALF_UP)
    if kind == 'amount_off':
        return min(gross, max(value, ZERO) * qty).quantize(CENT, ROUND_HALF_UP)
    return ZERO


def best_discounts(lines, promotions, now):
    live = [p for p in promotions if is_live(p, now)]
    out = []
    for line in lines:
        best, best_id = ZERO, None
        for p in live:
            if matches(p, line):
                d = discount_for(p, line)
                if d > best:
                    best, best_id = d, str(p.get('id'))
        out.append({'discount': best, 'promotion_id': best_id})
    return out


def as_dict(p):
    return {
        'id': str(p.id), 'name': p.name, 'kind': p.kind, 'value': str(p.value), 'applies_to': p.applies_to,
        'item_id': str(p.item_id) if p.item_id else None, 'category': p.category, 'min_quantity': p.min_quantity,
        'buy_quantity': p.buy_quantity, 'get_quantity': p.get_quantity,
        'starts_at': p.starts_at.isoformat() if p.starts_at else None,
        'ends_at': p.ends_at.isoformat() if p.ends_at else None,
        'is_active': p.is_active, 'is_deleted': p.is_deleted,
    }
