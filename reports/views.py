from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from rest_framework.views import APIView
<<<<<<< HEAD
from core.permissions import IsOwner
=======
from core.permissions import HasCapability
>>>>>>> 6f155c9 (Add expense support)
from core.utils import get_shops_for_user
from rest_framework.response import Response

from sales.models import Sale, SaleItem
from inventory.models import InventoryItem, StockBatch
from liabilities.models import Liability
from repairs.models import RepairPayment
from .utils import parse_period


def _shops_for(request):
    """Every report view MUST call this and filter every queryset by it.

    Before this helper existed, every view in this file queried its models
    with no shop filter at all — Sale.objects.filter(date__gte=...) with
    nothing scoping it to a shop, same for SaleItem/StockBatch/
    InventoryItem/Liability. That meant every report (sales summary, best
    sellers, tax, net worth, all of it) silently aggregated EVERY shop on
    the entire server together — a paying customer's dashboard was
    including every other customer's sales and liabilities in the same
    numbers, invisibly. Reuses the exact same get_shops_for_user used by
    ShopScopedMixin elsewhere, so a CEO with several branches still gets
    correct cross-branch aggregation within THEIR OWN organization, and
    the X-Branch-ID header still narrows to one branch the same way it
    does for every other screen — this is not a new isolation rule, it's
    applying the one rule that already existed everywhere else in the
    codebase, which this file alone had never been calling.
    """
    return get_shops_for_user(request.user, branch_id=request.headers.get('X-Branch-ID'))


class SalesSummaryView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, granularity = parse_period(request)
        # Item-level totals (revenue, profit, units) come from SaleItem;
        # "number of sales" counts cart headers, not lines.
        item_qs = SaleItem.objects.filter(
            sale__shop__in=shops, sale__date__gte=start, sale__date__lt=end, is_deleted=False, sale__is_deleted=False
        ).select_related('sale')
        sale_qs = Sale.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)
        repair_qs = RepairPayment.objects.filter(shop__in=shops, date__gte=start, date__lt=end)

        product_sales = sum(i.total for i in item_qs)
        service_revenue = sum(p.amount for p in repair_qs)
        total_sales = product_sales + service_revenue
        gross_profit = sum(i.profit for i in item_qs)
        number_of_sales = sale_qs.count()
        items_sold = sum(i.quantity for i in item_qs)

        buckets = defaultdict(lambda: {'sales': 0, 'count': 0})
        if granularity == 'day':
            for i in item_qs:
                local_dt = timezone.localtime(i.sale.date)
                buckets[local_dt.hour]['sales'] += float(i.total)
            for s in sale_qs:
                local_dt = timezone.localtime(s.date)
                buckets[local_dt.hour]['count'] += 1
            for p in repair_qs:
                local_dt = timezone.localtime(p.date)
                buckets[local_dt.hour]['sales'] += float(p.amount)
            series = [{'label': f'{h}:00', 'sales': buckets[h]['sales'], 'count': buckets[h]['count']} for h in range(24)]
        else:
            for i in item_qs:
                local_dt = timezone.localtime(i.sale.date)
                buckets[local_dt.day]['sales'] += float(i.total)
            for s in sale_qs:
                local_dt = timezone.localtime(s.date)
                buckets[local_dt.day]['count'] += 1
            for p in repair_qs:
                local_dt = timezone.localtime(p.date)
                buckets[local_dt.day]['sales'] += float(p.amount)
            days_in_month = (end - start).days
            series = [{'label': str(d), 'sales': buckets[d]['sales'], 'count': buckets[d]['count']} for d in range(1, days_in_month + 1)]

        return Response({
            'period': label,
            'granularity': granularity,
            'total_sales': total_sales,
            'product_sales': product_sales,
            'service_revenue': service_revenue,
            'gross_profit': gross_profit,
            'number_of_sales': number_of_sales,
            'items_sold': items_sold,
            'series': series,
        })


class SalesByItemView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = SaleItem.objects.filter(sale__shop__in=shops, sale__date__gte=start, sale__date__lt=end, is_deleted=False, sale__is_deleted=False)

        grouped = defaultdict(lambda: {'total_sold': 0, 'gross_sale_amt': 0.0, 'cost_total': 0.0, 'gross_profit': 0.0, 'discount': 0.0, 'category': ''})
        for s in qs:
            g = grouped[s.item_name]
            g['total_sold'] += s.quantity
            g['gross_sale_amt'] += float(s.total)
            g['cost_total'] += float(s.unit_cost) * s.quantity
            g['gross_profit'] += float(s.profit)
            g['discount'] += float(s.discount)
            g['category'] = s.category

        rows = []
        for name, g in grouped.items():
            cost_price = round(g['cost_total'] / g['total_sold'], 2) if g['total_sold'] else 0
            margin = round((g['gross_profit'] / g['gross_sale_amt']) * 100, 1) if g['gross_sale_amt'] else 0
            rows.append({
                'item_name': name,
                'category': g['category'],
                'total_sold': g['total_sold'],
                'gross_sale_amt': g['gross_sale_amt'],
                'cost_price': cost_price,
                'gross_profit': g['gross_profit'],
                'discount': g['discount'],
                'margin': margin,
            })
        rows.sort(key=lambda r: r['gross_sale_amt'], reverse=True)
        return Response({'period': label, 'rows': rows})


class BestSellingView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = SaleItem.objects.filter(sale__shop__in=shops, sale__date__gte=start, sale__date__lt=end, is_deleted=False, sale__is_deleted=False)

        grouped = defaultdict(lambda: {'total_sold': 0, 'gross_sale_amt': 0.0})
        for s in qs:
            g = grouped[s.item_name]
            g['total_sold'] += s.quantity
            g['gross_sale_amt'] += float(s.total)

        rows = [{'item_name': name, **g} for name, g in grouped.items()]
        rows.sort(key=lambda r: r['total_sold'], reverse=True)
        for idx, r in enumerate(rows[:20], start=1):
            r['rank'] = idx
        return Response({'period': label, 'rows': rows[:20]})


class SalesByCategoryView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = SaleItem.objects.filter(sale__shop__in=shops, sale__date__gte=start, sale__date__lt=end, is_deleted=False, sale__is_deleted=False)

        grouped = defaultdict(lambda: {'total_sold': 0, 'gross_sale_amt': 0.0, 'gross_profit': 0.0})
        for s in qs:
            key = s.category or 'uncategorized'
            g = grouped[key]
            g['total_sold'] += s.quantity
            g['gross_sale_amt'] += float(s.total)
            g['gross_profit'] += float(s.profit)

        rows = [{'category': cat, **g} for cat, g in grouped.items()]
        rows.sort(key=lambda r: r['gross_sale_amt'], reverse=True)
        return Response({'period': label, 'rows': rows})


class SalesByStaffView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = Sale.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)

        grouped = defaultdict(lambda: {'number_of_sales': 0, 'gross_sale_amt': 0.0, 'gross_profit': 0.0})
        for s in qs:
            key = s.staff_name or 'Unassigned'
            g = grouped[key]
            g['number_of_sales'] += 1
            g['gross_sale_amt'] += float(s.total)
            g['gross_profit'] += float(s.profit)

        rows = [{'staff_name': name, **g} for name, g in grouped.items()]
        rows.sort(key=lambda r: r['gross_sale_amt'], reverse=True)
        return Response({'period': label, 'rows': rows})


class PaymentMethodView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = Sale.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)

        grouped = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for s in qs:
            g = grouped[s.payment_method]
            g['count'] += 1
            g['total'] += float(s.total)

        rows = [{'payment_method': pm, **g} for pm, g in grouped.items()]
        rows.sort(key=lambda r: r['total'], reverse=True)
        return Response({'period': label, 'rows': rows})


class SalesByCustomerView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        qs = Sale.objects.filter(shop__in=shops, date__gte=start, date__lt=end, is_deleted=False)

        grouped = defaultdict(lambda: {'number_of_sales': 0, 'total_spent': 0.0, 'outstanding_balance': 0.0})
        for s in qs:
            key = s.customer_name or 'Walk-in'
            g = grouped[key]
            g['number_of_sales'] += 1
            g['total_spent'] += float(s.total)
            g['outstanding_balance'] += float(s.balance_due)

        rows = [{'customer_name': name, **g} for name, g in grouped.items()]
        rows.sort(key=lambda r: r['total_spent'], reverse=True)
        return Response({'period': label, 'rows': rows})


class TaxReportView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        start, end, label, _ = parse_period(request)
        # tax_rate lives on SaleItem (per line), not the Sale header — a
        # single cart can mix taxed and untaxed items.
        qs = SaleItem.objects.filter(sale__shop__in=shops, sale__date__gte=start, sale__date__lt=end, is_deleted=False, sale__is_deleted=False)

        grouped = defaultdict(lambda: {'taxable_sales': 0.0, 'tax_collected': 0.0, 'count': 0})
        total_tax = 0.0
        for s in qs:
            key = float(s.tax_rate)
            g = grouped[key]
            g['taxable_sales'] += float(s.subtotal - s.discount)
            g['tax_collected'] += float(s.tax_amount)
            g['count'] += 1
            total_tax += float(s.tax_amount)

        rows = [{'tax_rate': rate, **g} for rate, g in grouped.items()]
        rows.sort(key=lambda r: r['tax_rate'])
        return Response({'period': label, 'total_tax_collected': total_tax, 'rows': rows})


class ExpiringInventoryView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        days = int(request.query_params.get('days', 30))
        today = timezone.localdate()
        horizon = today + timedelta(days=days)

        batches = StockBatch.objects.filter(
            shop__in=shops, quantity_remaining__gt=0, expiry_date__isnull=False, expiry_date__lte=horizon, is_deleted=False
        ).select_related('item').order_by('expiry_date')

        rows = []
        for b in batches:
            days_left = (b.expiry_date - today).days
            rows.append({
                'item_name': b.item.name,
                'batch_number': b.batch_number,
                'quantity_remaining': b.quantity_remaining,
                'expiry_date': b.expiry_date,
                'days_left': days_left,
                'is_expired': days_left < 0,
            })
        return Response({'horizon_days': days, 'rows': rows})


class InventoryValuationView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        items = InventoryItem.objects.filter(shop__in=shops, is_deleted=False)
        rows = []
        total_inventory_value = 0.0
        total_selling_value = 0.0

        for i in items:
            qty = i.quantity
            cost = float(i.cost_price)
            sell = float(i.sell_price)
            inv_value = qty * cost
            sell_value = qty * sell
            potential_profit = sell_value - inv_value
            margin = round((potential_profit / sell_value) * 100, 2) if sell_value else 0

            total_inventory_value += inv_value
            total_selling_value += sell_value

            rows.append({
                'item_name': i.name,
                'category': i.category,
                'in_stock': qty,
                'cost': cost,
                'inventory_value': inv_value,
                'total_selling_price_value': sell_value,
                'potential_profit': potential_profit,
                'margin': margin,
            })

        potential_profit_total = total_selling_value - total_inventory_value
        overall_margin = round((potential_profit_total / total_selling_value) * 100, 2) if total_selling_value else 0

        return Response({
            'total_inventory_value': total_inventory_value,
            'total_selling_price_value': total_selling_value,
            'potential_profit': potential_profit_total,
            'margin': overall_margin,
            'rows': rows,
        })


class NetWorthView(APIView):
<<<<<<< HEAD
    permission_classes = [IsOwner]
=======
    permission_classes = [HasCapability('view_financial_reports')]
>>>>>>> 6f155c9 (Add expense support)

    def get(self, request):
        shops = _shops_for(request)
        inventory_value = sum(i.stock_value for i in InventoryItem.objects.filter(shop__in=shops, is_deleted=False))
        receivables = sum(s.balance_due for s in Sale.objects.filter(shop__in=shops, status='outstanding', is_deleted=False))
        assets = float(inventory_value) + float(receivables)

<<<<<<< HEAD
        liabilities_total = sum(
            float(l.amount) for l in Liability.objects.filter(shop__in=shops, status='pending', is_deleted=False)
        )
=======
        from django.db.models import Sum, Q
        outstanding_liabilities = Liability.objects.filter(shop__in=shops, is_deleted=False).exclude(status='cleared').annotate(
            paid=Sum('payments__amount', filter=Q(payments__is_deleted=False))
        )
        liabilities_total = sum(float(l.amount - (l.paid or 0)) for l in outstanding_liabilities)
>>>>>>> 6f155c9 (Add expense support)

        return Response({
            'inventory_value': float(inventory_value),
            'accounts_receivable': float(receivables),
            'assets': assets,
            'liabilities': liabilities_total,
            'net_worth': assets - liabilities_total,
        })
