"""
Business Intelligence endpoints — Executive Overview, Expense Analytics,
Liability Analytics. Kept in a separate module from reports/views.py
(the older single-day/single-month sales & inventory reports) rather than
mixed into it: these are genuinely a different layer (multi-period,
comparison-aware, gated by the new 'view_financial_reports' capability
instead of a flat IsOwner), and keeping them apart means the existing,
already-working reports can't be destabilized by this addition.

Sales/SaleItem math (revenue, COGS, gross profit) deliberately mirrors
reports/views.py's SalesSummaryView exactly — same .total/.profit
properties, same in-memory aggregation over the filtered queryset — so
Executive Overview can never show a different "revenue" for the same
period than the Sales report does. See sales.models.SaleItem for why
these are Python properties (rounding logic) rather than stored columns,
which is also why this doesn't do the aggregation in raw SQL.

Expense/Liability numbers, by contrast, ARE plain stored Decimal columns
on brand-new tables, so those use real Sum()/annotate() aggregation —
there's no existing convention to stay consistent with there, and no
reason not to do it properly from the start.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Count, F, DecimalField, Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from core.permissions import HasCapability
from expenses.models import Expense
from liabilities.models import Liability, LiabilityPayment
from sales.models import Sale, SaleItem
from inventory.models import StockBatch
from .utils import parse_range_period
from .views import _shops_for

ZERO = Decimal('0')


def _period_or_400(request):
    """Returns (period_dict, error_response). error_response is None on
    success — callers do `period, err = _period_or_400(request); if err:
    return err`, so every view handles a bad ?period=/?start=/?end= the
    same way instead of duplicating the try/except."""
    try:
        return parse_range_period(request), None
    except ValueError as exc:
        return None, Response({'detail': str(exc)}, status=400)


def pct_change(current, previous):
    """None when there's no previous-period baseline to compare against —
    0 -> something or something -> 0 from a genuinely empty previous
    period is reported as "not enough data" rather than a misleading
    +/-100% or a divide-by-zero."""
    if previous == 0:
        return None
    return float((current - previous) / previous * 100)


def money_block(current, previous):
    return {'value': current, 'previous': previous, 'change_pct': pct_change(current, previous)}


def sales_financials(shops, start, end):
    """Revenue (tax-inclusive — matches SalesSummaryView's definition
    exactly), COGS, gross profit, transaction count and average sale, for
    completed product sales in [start, end)."""
    sale_qs = Sale.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)
    item_qs = SaleItem.objects.filter(sale__in=sale_qs, is_deleted=False).select_related('sale')

    revenue = cogs = gross_profit = ZERO
    for i in item_qs:
        revenue += i.total
        cogs += i.unit_cost * i.quantity
        gross_profit += i.profit

    transaction_count = sale_qs.count()
    avg_transaction_value = (revenue / transaction_count) if transaction_count else ZERO
    return {
        'revenue': revenue, 'cogs': cogs, 'gross_profit': gross_profit,
        'transaction_count': transaction_count, 'avg_transaction_value': avg_transaction_value,
    }


def service_financials(shops, start, end):
    """Repair/service revenue and parts cost in [start, end). Zero for any
    shop that doesn't use the service module — harmless, not an error.
    Payment date and parts-added date aren't guaranteed to land in the
    same period for a ticket that spans several days; this is a
    period-bucketed cash-basis view, same convention the rest of this
    system already uses (an Expense or LiabilityPayment is counted in the
    period it happened in, not matched against some other event's date),
    not a claim of strict accrual matching."""
    from repairs.models import RepairPayment, RepairPart

    revenue = RepairPayment.objects.filter(
        shop__in=shops, date__gte=start, date__lt=end, is_deleted=False,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    cogs = RepairPart.objects.filter(
        shop__in=shops, added_at__gte=start, added_at__lt=end, is_deleted=False,
    ).aggregate(t=Sum(F('unit_cost') * F('quantity'), output_field=DecimalField()))['t'] or ZERO
    return {'revenue': revenue, 'cogs': cogs, 'gross_profit': revenue - cogs}


def expense_financials(shops, start, end):
    """Total business cost in [start, end): direct Expense rows PLUS
    LiabilityPayments whose liability category is a genuine operating
    expense (salary/rent/utility/other — see LiabilityPayment.EXPENSE_CATEGORIES).
    A Liability itself is never counted here, only its payments — see
    expenses.models.Expense's docstring for why double-counting is
    structurally impossible under this split."""
    direct = Expense.objects.filter(
        shop__in=shops, date__gte=start.date(), date__lt=end.date(), is_deleted=False,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    from_liabilities = LiabilityPayment.objects.filter(
        shop__in=shops, paid_at__gte=start, paid_at__lt=end, is_deleted=False,
        liability__category__in=LiabilityPayment.EXPENSE_CATEGORIES,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    return {'direct': direct, 'from_liability_payments': from_liabilities, 'total': direct + from_liabilities}


def inventory_value_now(shops):
    """Current stock value (not period-scoped — this is a balance, like
    outstanding liabilities, not a flow; comparing it to a "previous
    period" value would require snapshotting history this system doesn't
    keep, so it's reported as a single current figure, same as the
    existing InventoryValuationView already does)."""
    batches = StockBatch.objects.filter(shop__in=shops, is_deleted=False, quantity_remaining__gt=0)
    return batches.aggregate(
        t=Sum(F('quantity_remaining') * F('cost_price'), output_field=DecimalField())
    )['t'] or ZERO


def outstanding_liabilities_now(shops):
    qs = Liability.objects.filter(shop__in=shops, is_deleted=False).exclude(status='cleared').annotate(
        paid=Sum('payments__amount', filter=Q(payments__is_deleted=False))
    )
    return sum((l.amount - (l.paid or ZERO) for l in qs), ZERO)


class ExecutiveOverviewView(APIView):
    """GET /api/reports/analytics/overview/?period=this_month — the
    headline KPI set: revenue, COGS, gross profit, total expenses, net
    profit, outstanding liabilities, inventory value, transaction count,
    average sale — each compared against the immediately-preceding period
    of the same length where that comparison makes sense (a flow, not a
    balance — see inventory_value_now/outstanding_liabilities_now)."""
    permission_classes = [HasCapability('view_financial_reports')]

    def get(self, request):
        period, err = _period_or_400(request)
        if err:
            return err
        shops = _shops_for(request)
        start, end = period['start'], period['end']
        prev_start, prev_end = period['previous_start'], period['previous_end']

        sales_now, sales_prev = sales_financials(shops, start, end), sales_financials(shops, prev_start, prev_end)
        service_now, service_prev = service_financials(shops, start, end), service_financials(shops, prev_start, prev_end)
        expenses_now, expenses_prev = expense_financials(shops, start, end), expense_financials(shops, prev_start, prev_end)

        revenue_now = sales_now['revenue'] + service_now['revenue']
        revenue_prev = sales_prev['revenue'] + service_prev['revenue']
        cogs_now = sales_now['cogs'] + service_now['cogs']
        cogs_prev = sales_prev['cogs'] + service_prev['cogs']
        gross_profit_now = sales_now['gross_profit'] + service_now['gross_profit']
        gross_profit_prev = sales_prev['gross_profit'] + service_prev['gross_profit']
        net_profit_now = gross_profit_now - expenses_now['total']
        net_profit_prev = gross_profit_prev - expenses_prev['total']
        transactions_now = sales_now['transaction_count']
        transactions_prev = sales_prev['transaction_count']

        return Response({
            'period': {'label': period['label'], 'start': start, 'end': end},
            'revenue': money_block(revenue_now, revenue_prev),
            'cogs': money_block(cogs_now, cogs_prev),
            'gross_profit': money_block(gross_profit_now, gross_profit_prev),
            'total_expenses': money_block(expenses_now['total'], expenses_prev['total']),
            'net_profit': money_block(net_profit_now, net_profit_prev),
            'transaction_count': {
                'value': transactions_now, 'previous': transactions_prev,
                'change_pct': pct_change(transactions_now, transactions_prev),
            },
            'avg_transaction_value': money_block(sales_now['avg_transaction_value'], sales_prev['avg_transaction_value']),
            # Balances, not flows -- current snapshot only, see the two helpers' docstrings.
            'outstanding_liabilities': {'value': outstanding_liabilities_now(shops)},
            'inventory_value': {'value': inventory_value_now(shops)},
        })


class ExpenseAnalyticsView(APIView):
    """GET /api/reports/analytics/expenses/?period=this_month — total,
    trend, and breakdowns by category/payment method, direct Expenses
    only (a Liability payment is a liability-analytics concern — see
    LiabilityAnalyticsView — even though it's also folded into Executive
    Overview's total_expenses; keeping this view to Expense rows only
    means "expenses by category" never has to invent a fake category for
    a liability payment that was never categorized as an expense category
    to begin with)."""
    permission_classes = [HasCapability('view_financial_reports')]

    def get(self, request):
        period, err = _period_or_400(request)
        if err:
            return err
        shops = _shops_for(request)
        start, end = period['start'].date(), period['end'].date()
        prev_start, prev_end = period['previous_start'].date(), period['previous_end'].date()

        qs = Expense.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)
        prev_qs = Expense.objects.filter(shop__in=shops, date__gte=prev_start, date__lt=prev_end, is_deleted=False)

        total = qs.aggregate(t=Sum('amount'))['t'] or ZERO
        prev_total = prev_qs.aggregate(t=Sum('amount'))['t'] or ZERO

        by_category = list(
            qs.values('category__name').annotate(total=Sum('amount'), count=Count('id')).order_by('-total')
        )
        by_payment_method = list(
            qs.values('payment_method').annotate(total=Sum('amount')).order_by('-total')
        )
        by_day = list(
            qs.values('date').annotate(total=Sum('amount')).order_by('date')
        )

        return Response({
            'period': {'label': period['label'], 'start': start, 'end': end},
            'total': money_block(total, prev_total),
            'by_category': [
                {'category': r['category__name'], 'total': r['total'], 'count': r['count']} for r in by_category
            ],
            'by_payment_method': by_payment_method,
            'trend': by_day,
        })


class LiabilityAnalyticsView(APIView):
    """GET /api/reports/analytics/liabilities/ — current standing (not
    period-scoped, same balance-vs-flow reasoning as Executive Overview's
    outstanding_liabilities) plus payments made in the optional
    ?period=... window, for the "how much did we pay off recently" flow
    view. Cleared liabilities stay fully visible here (see the Liability
    model docstring on why history is never discarded) rather than only
    showing what's currently owed."""
    permission_classes = [HasCapability('view_financial_reports')]

    def get(self, request):
        period, err = _period_or_400(request)
        if err:
            return err
        shops = _shops_for(request)
        today = timezone.localdate()

        all_liabilities = Liability.objects.filter(shop__in=shops, is_deleted=False).annotate(
            paid=Sum('payments__amount', filter=Q(payments__is_deleted=False))
        )
        outstanding_total = ZERO
        by_category = {}
        by_status = {'unpaid': ZERO, 'partially_paid': ZERO, 'cleared': ZERO}
        overdue_total = ZERO

        for liability in all_liabilities:
            outstanding = liability.amount - (liability.paid or ZERO)
            # Original amount, not outstanding, per status -- outstanding
            # for a cleared liability is always 0 by definition, which
            # would make by_status['cleared'] trivially zero and defeat
            # the point of showing it at all. This way it reports "how
            # much liability value has been cleared historically,"
            # exactly the number the "cleared liabilities must remain
            # visible" requirement is asking for.
            by_status[liability.status] = by_status.get(liability.status, ZERO) + liability.amount
            if liability.status != 'cleared':
                outstanding_total += outstanding
                by_category[liability.category] = by_category.get(liability.category, ZERO) + outstanding
                if liability.due_date and liability.due_date < today:
                    overdue_total += outstanding

        upcoming = (
            Liability.objects.filter(shop__in=shops, is_deleted=False, due_date__gte=today, due_date__lte=today + timedelta(days=14))
            .exclude(status='cleared')
            .annotate(paid=Sum('payments__amount', filter=Q(payments__is_deleted=False)))
            .order_by('due_date')
            .values('id', 'name', 'category', 'due_date', 'amount', 'paid')[:10]
        )

        payments_qs = LiabilityPayment.objects.filter(
            shop__in=shops, is_deleted=False, paid_at__gte=period['start'], paid_at__lt=period['end'],
        )
        payments_total = payments_qs.aggregate(t=Sum('amount'))['t'] or ZERO

        return Response({
            'period': {'label': period['label'], 'start': period['start'], 'end': period['end']},
            'outstanding_total': outstanding_total,
            'overdue_total': overdue_total,
            'by_status': by_status,
            'by_category': by_category,
            'upcoming_due': [{**u, 'outstanding': u['amount'] - (u['paid'] or ZERO)} for u in upcoming],
            'payments_in_period': money_block(payments_total, ZERO),
        })
