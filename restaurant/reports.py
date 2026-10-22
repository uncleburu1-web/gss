"""Reading finished restaurant orders, and the restaurant summary (docs/restaurant/API_SPECIFICATION.md §1).

  GET /api/restaurant/orders/?status=&date=&from=&to=&table=   closed and cancelled orders
  GET /api/restaurant/orders/{id}/                              one order in full
  GET /api/reports/restaurant/summary/?from=&to=                covers, average bill, takings by service
                                                                type and payment method, service charge,
                                                                taxes by rule, tips, discounts, voids and
                                                                waste, kitchen times by station, top items

One branch at a time (resolved like every other restaurant request; a CEO names it with X-Branch-ID).
Dates are the hub's BUSINESS days (an order at 01:00 belongs to the evening before). Who sees what
follows the sales rules: `view_all_sales` sees every order, anyone else only orders they took or
were paid for; the summary needs `view_financial_reports`. All figures come from the frozen bills.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.capabilities import has_capability

from .models import BillCharge, BillPayment, KitchenTicketItem, Order, OrderItem
from .permissions import RestaurantAccess, resolve_branch

ZERO = Decimal('0.00')


def _money(d):
    return f'{(d or ZERO).quantize(Decimal("0.01"))}'


def _days(request):
    """(first, last) business days from ?date= or ?from=&to=; today when none given."""
    q = request.query_params
    one = q.get('date')
    first, last = (one, one) if one else (q.get('from'), q.get('to'))
    today = timezone.localdate()
    first = parse_date(first) if first else today
    last = parse_date(last) if last else first
    if first is None or last is None:
        raise ValidationError({'detail': 'Dates are YYYY-MM-DD.', 'code': 'bad_date'})
    if last < first:
        raise ValidationError({'detail': 'The end date is before the start date.', 'code': 'bad_date'})
    if (last - first) > timedelta(days=366):
        raise ValidationError({'detail': 'Ask for a year or less at a time.', 'code': 'range_too_long'})
    return first, last


def _visible_orders(request, branch):
    qs = Order.objects.filter(shop=branch, is_deleted=False)
    if has_capability(request.user, 'view_all_sales'):
        return qs
    me = request.user.username
    return qs.filter(Q(waiter=me) | Q(opened_by=me) | Q(bill__payments__received_by=me)).distinct()


def _order_row(o):
    bill = getattr(o, 'bill', None)
    return {
        'id': str(o.id), 'number': o.number, 'status': o.status, 'service_type': o.service_type,
        'table': o.table.label if o.table else None, 'guests': o.guests, 'waiter': o.waiter, 'business_date': o.business_date.isoformat(),
        'opened_at': o.opened_at, 'closed_at': o.closed_at, 'cancel_reason': o.cancel_reason,
        'total': _money(bill.total) if bill else None, 'sale_id': str(o.sale_id) if o.sale_id else None,
    }


class OrderListView(APIView):
    permission_classes = [RestaurantAccess]

    def get(self, request):
        branch = resolve_branch(request)
        first, last = _days(request)
        qs = _visible_orders(request, branch).filter(business_date__gte=first, business_date__lte=last)
        status_param = request.query_params.get('status')
        if status_param:
            if status_param not in ('closed', 'cancelled'):
                raise ValidationError({'detail': 'Status is closed or cancelled. Open orders live on the hub.', 'code': 'bad_status'})
            qs = qs.filter(status=status_param)
        if request.query_params.get('table'):
            qs = qs.filter(table__label=request.query_params['table'])
        qs = qs.select_related('table', 'bill').order_by('-closed_at', '-opened_at')[:500]
        return Response({'from': first, 'to': last, 'results': [_order_row(o) for o in qs]})


class OrderDetailView(APIView):
    permission_classes = [RestaurantAccess]

    def get(self, request, pk):
        branch = resolve_branch(request)
        o = _visible_orders(request, branch).select_related('table', 'bill').filter(pk=pk).first()
        if o is None:
            raise NotFound({'detail': 'Order not found.', 'code': 'not_found'})  # also when it is another branch's
        bill = getattr(o, 'bill', None)
        return Response({
            **_order_row(o), 'opened_by': o.opened_by, 'notes': o.notes, 'pricing': o.pricing,
            'items': [{
                'id': str(i.id), 'name': i.name, 'unit_price': _money(i.unit_price), 'modifiers_price': _money(i.modifiers_price),
                'quantity': str(i.quantity), 'voided_quantity': str(i.voided_quantity), 'state': i.state, 'waste': i.waste,
                'void_reason': i.void_reason, 'notes': i.notes, 'modifiers': [{'name': m.name, 'price_delta': _money(m.price_delta)} for m in i.modifiers.all()],
            } for i in o.items.all()],
            'tickets': [{
                'id': str(t.id), 'station': t.station.name if t.station else None, 'number': t.number, 'kind': t.kind, 'sent_at': t.sent_at,
                'items': [{'order_item_id': str(ti.order_item_id), 'quantity': str(ti.quantity), 'state': ti.state,
                           'started_at': ti.started_at, 'ready_at': ti.ready_at, 'served_at': ti.served_at} for ti in t.items.all()],
            } for t in o.tickets.select_related('station')],
            'bill': bill and {
                'number': bill.number, 'subtotal': _money(bill.subtotal), 'discount': _money(bill.discount), 'discount_reason': bill.discount_reason,
                'net': _money(bill.net), 'total': _money(bill.total),
                'charges': [{'kind': c.kind, 'name': c.name, 'rate': str(c.rate), 'inclusive': c.inclusive, 'base': _money(c.base), 'amount': _money(c.amount)}
                            for c in bill.charges.all()],
                'payments': [{'method': p.method, 'amount': _money(p.amount), 'tip': _money(p.tip), 'received_by': p.received_by, 'received_at': p.received_at}
                             for p in bill.payments.all()],
            },
        })


class RestaurantSummaryView(APIView):
    permission_classes = [RestaurantAccess]

    def get(self, request):
        if not has_capability(request.user, 'view_financial_reports'):
            raise PermissionDenied('You do not have access to financial reports.')
        branch = resolve_branch(request)
        first, last = _days(request)
        orders = Order.objects.filter(shop=branch, is_deleted=False, business_date__gte=first, business_date__lte=last)
        closed = orders.filter(status='closed').select_related('bill')

        takings = ZERO
        covers = 0
        by_type = defaultdict(lambda: {'orders': 0, 'takings': ZERO})
        for o in closed:
            total = o.bill.total if getattr(o, 'bill', None) else ZERO
            takings += total
            covers += o.guests or 0
            by_type[o.service_type]['orders'] += 1
            by_type[o.service_type]['takings'] += total
        n = closed.count()

        payments = BillPayment.objects.filter(bill__order__in=closed)
        by_method, tips = defaultdict(lambda: ZERO), ZERO
        for p in payments:
            by_method[p.method] += p.amount
            tips += p.tip

        service, discounts, taxes = ZERO, ZERO, defaultdict(lambda: {'base': ZERO, 'amount': ZERO})
        for c in BillCharge.objects.filter(bill__order__in=closed):
            if c.kind == 'service_charge':
                service += c.amount
            else:
                t = taxes[(c.name, c.rate, c.inclusive)]
                t['base'] += c.base
                t['amount'] += c.amount
        for o in closed:
            discounts += o.bill.discount if getattr(o, 'bill', None) else ZERO

        voided = OrderItem.objects.filter(order__in=orders, voided_quantity__gt=0)
        void_qty = sum((i.voided_quantity for i in voided), Decimal('0'))
        waste = [i for i in voided if i.waste]
        waste_value = sum(((i.unit_price + i.modifiers_price) * i.voided_quantity for i in waste), ZERO)

        minutes = defaultdict(list)
        for ti in KitchenTicketItem.objects.filter(ticket__order__in=closed, ready_at__isnull=False, ticket__kind='new').select_related('ticket__station'):
            name = ti.ticket.station.name if ti.ticket.station else 'No station'
            minutes[name].append((ti.ready_at - ti.ticket.sent_at).total_seconds() / 60)

        top = defaultdict(lambda: {'quantity': Decimal('0'), 'amount': ZERO})
        for i in OrderItem.objects.filter(order__in=closed):
            left = i.quantity - i.voided_quantity
            if left > 0:
                top[i.name]['quantity'] += left
                top[i.name]['amount'] += ((i.unit_price + i.modifiers_price) * left)

        return Response({
            'branch': str(branch.id), 'from': first, 'to': last,
            'orders': n, 'cancelled': orders.filter(status='cancelled').count(), 'covers': covers,
            'takings': _money(takings), 'average_bill': _money(takings / n if n else ZERO),
            'by_service_type': {k: {'orders': v['orders'], 'takings': _money(v['takings'])} for k, v in sorted(by_type.items())},
            'by_payment_method': {k: _money(v) for k, v in sorted(by_method.items())},
            'service_charge': _money(service), 'discounts': _money(discounts), 'tips': _money(tips),
            'taxes': [{'name': name, 'rate': str(rate), 'inclusive': inclusive, 'base': _money(t['base']), 'amount': _money(t['amount'])}
                      for (name, rate, inclusive), t in sorted(taxes.items(), key=lambda kv: (kv[0][1], kv[0][0]))],
            'voids': {'lines': voided.count(), 'quantity': str(void_qty), 'waste_lines': len(waste), 'waste_value': _money(waste_value)},
            'kitchen_minutes': {k: {'tickets_items': len(v), 'average': round(sum(v) / len(v), 1)} for k, v in sorted(minutes.items())},
            'top_items': [{'name': k, 'quantity': str(v['quantity']), 'amount': _money(v['amount'])}
                          for k, v in sorted(top.items(), key=lambda kv: -kv[1]['amount'])[:10]],
        })
