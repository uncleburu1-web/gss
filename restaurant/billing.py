"""The restaurant bill: subtotal, discount, service charge and taxes.

Pure function, no database. Written identically in JavaScript for the hub
(gssdesktop/src/main/restaurant/billing.js) and held identical by billing_vectors.json, whose
expected figures were worked out by hand (ADR-018, ADR-022). Rules (docs/restaurant/REQUIREMENTS.md §4):

  * line = (unit price + modifiers) x (quantity - voided), rounded half-up to the kobo;
    subtotal = sum of lines.
  * discount: an amount (never more than the subtotal) or a percent of the subtotal (rounded);
    it is shared across items in proportion to their value.
  * prices may already include taxes ("inclusive" rules). The amount BEFORE those taxes is what
    service charge and added ("exclusive") taxes are calculated on: a tax on "the bill excluding VAT"
    when VAT is inside the price is calculated on the price without the VAT.
  * service charge: rate x the pre-tax net, only for the service types it is set for.
  * an inclusive tax is shown, not added: amount = covered net x rate / (100 + all inclusive rates
    covering the same class). An exclusive tax is added: rate x (covered pre-tax net + the service
    charge if the rule covers it).
  * every amount is computed exactly and rounded half-up to the kobo once, on its own line.
  * a rule applies when it is active and the business date is inside its validity.

Amounts come in and go out as strings ("4800.00"), so both languages compare exactly.
"""
from datetime import date
from fractions import Fraction


class BillError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _f(value):
    return Fraction(str(value if value not in (None, '') else '0'))


def _round_kobo(value):
    """Half-up to the kobo (all amounts here are >= 0). Returns a Fraction of whole kobo / 100."""
    kobo = value * 100
    return Fraction((kobo.numerator * 2 + kobo.denominator) // (kobo.denominator * 2), 100)


def _money(value):
    kobo = int(value * 100)
    return f'{kobo // 100}.{kobo % 100:02d}'


def _rate(value):
    scaled = _f(value) * 10000
    if scaled.denominator != 1:
        raise BillError('rate_precision')
    n = int(scaled)
    return f'{n // 10000}.{n % 10000:04d}'


def _applies(rule, business_date):
    if not rule.get('is_active'):
        return False
    day = date.fromisoformat(business_date)
    if date.fromisoformat(rule['valid_from']) > day:
        return False
    return not rule.get('valid_to') or day <= date.fromisoformat(rule['valid_to'])


def _covers(rule, tax_class):
    classes = rule.get('tax_classes') or []
    return not classes or tax_class in classes


def compute_bill(data):
    lines = data.get('lines') or []
    by_class = {}
    subtotal = Fraction(0)
    for line in lines:
        quantity = _f(line.get('quantity', '1')) - _f(line.get('voided_quantity'))
        if quantity < 0:
            raise BillError('voided_more_than_ordered')
        amount = _round_kobo((_f(line['unit_price']) + _f(line.get('modifiers_price'))) * quantity)
        subtotal += amount
        by_class[line.get('tax_class') or ''] = by_class.get(line.get('tax_class') or '', Fraction(0)) + amount

    discount = Fraction(0)
    spec = data.get('discount')
    if spec:
        if spec['type'] == 'percent':
            discount = _round_kobo(subtotal * _f(spec['value']) / 100)
        else:
            discount = _round_kobo(_f(spec['value']))
        if discount > subtotal:
            raise BillError('discount_too_large')
    net = subtotal - discount
    share = (net / subtotal) if subtotal else Fraction(0)
    net_by_class = {c: v * share for c, v in by_class.items()}

    rules = [r for r in (data.get('tax_rules') or []) if _applies(r, data['business_date'])]
    inclusive = [r for r in rules if r.get('inclusive')]
    inclusive_rate = {c: sum((_f(r['rate']) for r in inclusive if _covers(r, c)), Fraction(0)) for c in net_by_class}
    pretax_by_class = {c: v * 100 / (100 + inclusive_rate[c]) for c, v in net_by_class.items()}
    pretax = sum(pretax_by_class.values(), Fraction(0))

    charges = []
    service = Fraction(0)
    sc = data.get('service_charge')
    if sc and _f(sc.get('rate')) > 0 and data.get('service_type') in (sc.get('service_types') or []):
        service = _round_kobo(pretax * _f(sc['rate']) / 100)
        charges.append({'kind': 'service_charge', 'tax_rule_id': None, 'name': 'Service charge', 'rate': _rate(sc['rate']),
                        'inclusive': False, 'base': _money(_round_kobo(pretax)), 'amount': _money(service)})

    added = Fraction(0)
    for rule in rules:
        rate = _f(rule['rate'])
        if rule.get('inclusive'):
            covered = [c for c in net_by_class if _covers(rule, c)]
            base = sum((net_by_class[c] for c in covered), Fraction(0))
            amount = _round_kobo(sum((net_by_class[c] * rate / (100 + inclusive_rate[c]) for c in covered), Fraction(0)))
        else:
            base = sum((pretax_by_class[c] for c in pretax_by_class if _covers(rule, c)), Fraction(0))
            if rule.get('applies_to_service_charge'):
                base += service
            amount = _round_kobo(base * rate / 100)
            added += amount
        charges.append({'kind': 'tax', 'tax_rule_id': rule['id'], 'name': rule['name'], 'rate': _rate(rule['rate']),
                        'inclusive': bool(rule.get('inclusive')), 'base': _money(_round_kobo(base)), 'amount': _money(amount)})

    return {'subtotal': _money(subtotal), 'discount': _money(discount), 'net': _money(net),
            'charges': charges, 'total': _money(net + service + added)}


def compute_bill_or_error(data):
    try:
        return compute_bill(data)
    except BillError as exc:
        return {'error': exc.code}
