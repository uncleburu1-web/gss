<<<<<<< HEAD
from django.test import TestCase

# Create your tests here.
=======
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Organization, Shop
from staff.models import Worker
from inventory.models import InventoryItem, StockBatch
from expenses.models import Expense, ExpenseCategory
from liabilities.models import Liability, LiabilityPayment


class ExecutiveOverviewKnownNumbersTests(TestCase):
    """Every figure here is hand-computed against the fixtures below, not
    just asserted to be "some number" — the whole point of this view is
    that the numbers are trustworthy, not merely present. No tax/discount
    is involved (both default to zero), so SaleItem.total == unit_price *
    quantity exactly, with no rounding to account for.
    """

    def setUp(self):
        self.org = Organization.objects.create(name='Overview Test Shop', business_type='general')
        self.shop = Shop.objects.create(name='Overview Test Shop', organization=self.org)
        self.user = User.objects.create_user(username='owner_ov', password='x')
        self.org.owner = self.user
        self.org.save()
        Worker.objects.create(user=self.user, full_name='Owner', role='owner', shop=self.shop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # One sale: 2 units @ 1000, cost 400 each -> revenue 2000, COGS 800, gross profit 1200.
        item = InventoryItem.objects.create(shop=self.shop, name='Widget', sell_price=Decimal('1000'))
        StockBatch.objects.create(
            shop=self.shop, item=item, batch_number='B1', quantity_received=10, quantity_remaining=10,
            cost_price=Decimal('400'), selling_price=Decimal('1000'),
        )
        sale_res = self.client.post('/api/sales/', {
            'customer_name': 'Walk-in', 'payment_method': 'cash', 'status': 'completed',
            'items': [{
                'item': str(item.id), 'item_name': item.name, 'category': '', 'quantity': 2,
                'unit_price': '1000.00', 'unit_cost': 0,
            }],
        }, format='json')
        assert sale_res.status_code == 201, sale_res.data

        # A direct expense: 5000 fuel.
        cat_res = self.client.get('/api/expense-categories/')
        rows = cat_res.data['results'] if 'results' in cat_res.data else cat_res.data
        fuel_id = next(r['id'] for r in rows if r['name'] == 'Fuel')
        self.client.post('/api/expenses/', {
            'category': fuel_id, 'amount': '5000', 'payment_method': 'cash', 'date': timezone.localdate().isoformat(),
        }, format='json')

        # A salary liability payment of 3000 -- counts as an expense.
        salary_res = self.client.post('/api/liabilities/', {'name': 'Salary', 'category': 'salary', 'amount': '10000'}, format='json')
        self.client.post('/api/liability-payments/', {
            'liability': salary_res.data['id'], 'amount': '3000', 'payment_method': 'cash',
        }, format='json')

        # A loan payment of 2000 -- must NOT count as an expense.
        loan_res = self.client.post('/api/liabilities/', {'name': 'Loan', 'category': 'loan', 'amount': '10000'}, format='json')
        self.client.post('/api/liability-payments/', {
            'liability': loan_res.data['id'], 'amount': '2000', 'payment_method': 'cash',
        }, format='json')

    def test_overview_numbers_match_hand_calculation(self):
        res = self.client.get('/api/reports/analytics/overview/?period=today')
        self.assertEqual(res.status_code, 200)
        data = res.data

        self.assertEqual(Decimal(data['revenue']['value']), Decimal('2000.00'))
        self.assertEqual(Decimal(data['cogs']['value']), Decimal('800.00'))
        self.assertEqual(Decimal(data['gross_profit']['value']), Decimal('1200.00'))
        # Expenses: 5000 direct + 3000 salary liability payment. The 2000
        # loan payment must be excluded -- this is the double-counting
        # guard working end-to-end, not just at the unit level.
        self.assertEqual(Decimal(data['total_expenses']['value']), Decimal('8000.00'))
        self.assertEqual(Decimal(data['net_profit']['value']), Decimal('1200.00') - Decimal('8000.00'))
        self.assertEqual(data['transaction_count']['value'], 1)
        self.assertEqual(Decimal(data['avg_transaction_value']['value']), Decimal('2000.00'))
        # Outstanding liabilities: salary 10000-3000=7000, loan 10000-2000=8000 -> 15000 total.
        self.assertEqual(Decimal(data['outstanding_liabilities']['value']), Decimal('15000.00'))

    def test_seller_cannot_view_overview_by_default(self):
        seller = User.objects.create_user(username='seller_ov', password='x')
        Worker.objects.create(user=seller, full_name='Seller', role='seller', shop=self.shop)
        client = APIClient()
        client.force_authenticate(user=seller)
        res = client.get('/api/reports/analytics/overview/?period=today')
        self.assertEqual(res.status_code, 403)

    def test_bad_period_returns_400_not_500(self):
        res = self.client.get('/api/reports/analytics/overview/?period=not_a_real_period')
        self.assertEqual(res.status_code, 400)

    def test_custom_period_requires_dates(self):
        res = self.client.get('/api/reports/analytics/overview/?period=custom')
        self.assertEqual(res.status_code, 400)


class ExpenseAnalyticsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Expense Analytics Shop', business_type='general')
        self.shop = Shop.objects.create(name='Expense Analytics Shop', organization=self.org)
        self.user = User.objects.create_user(username='owner_ea', password='x')
        self.org.owner = self.user
        self.org.save()
        Worker.objects.create(user=self.user, full_name='Owner', role='owner', shop=self.shop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.fuel = ExpenseCategory.objects.create(shop=self.shop, name='Fuel')
        self.rent = ExpenseCategory.objects.create(shop=self.shop, name='Rent')
        today = timezone.localdate().isoformat()
        Expense.objects.create(shop=self.shop, category=self.fuel, amount=Decimal('3000'), date=today, payment_method='cash')
        Expense.objects.create(shop=self.shop, category=self.fuel, amount=Decimal('2000'), date=today, payment_method='pos')
        Expense.objects.create(shop=self.shop, category=self.rent, amount=Decimal('15000'), date=today, payment_method='transfer')

    def test_totals_and_breakdown_match(self):
        res = self.client.get('/api/reports/analytics/expenses/?period=today')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(res.data['total']['value']), Decimal('20000.00'))
        by_cat = {r['category']: Decimal(r['total']) for r in res.data['by_category']}
        self.assertEqual(by_cat['Fuel'], Decimal('5000.00'))
        self.assertEqual(by_cat['Rent'], Decimal('15000.00'))


class LiabilityAnalyticsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Liability Analytics Shop', business_type='general')
        self.shop = Shop.objects.create(name='Liability Analytics Shop', organization=self.org)
        self.user = User.objects.create_user(username='owner_la', password='x')
        self.org.owner = self.user
        self.org.save()
        Worker.objects.create(user=self.user, full_name='Owner', role='owner', shop=self.shop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_cleared_liability_still_counted_historically_not_in_outstanding(self):
        res = self.client.post('/api/liabilities/', {'name': 'Rent', 'category': 'rent', 'amount': '5000'}, format='json')
        self.client.post('/api/liability-payments/', {'liability': res.data['id'], 'amount': '5000', 'payment_method': 'cash'}, format='json')

        self.client.post('/api/liabilities/', {'name': 'Supplier', 'category': 'supplier_credit', 'amount': '8000'}, format='json')

        analytics_res = self.client.get('/api/reports/analytics/liabilities/')
        self.assertEqual(analytics_res.status_code, 200)
        self.assertEqual(Decimal(analytics_res.data['outstanding_total']), Decimal('8000.00'))
        # The cleared rent liability's full original value (5000) still
        # shows up under by_status['cleared'] -- it isn't simply erased
        # once paid off, satisfying "cleared liabilities must remain
        # visible in historical reports."
        self.assertEqual(Decimal(analytics_res.data['by_status']['cleared']), Decimal('5000.00'))
        self.assertEqual(Decimal(analytics_res.data['by_status']['unpaid']), Decimal('8000.00'))
        # But it's correctly EXCLUDED from outstanding-by-category, since
        # nothing is actually still owed on it.
        self.assertNotIn('rent', analytics_res.data['by_category'])
        self.assertEqual(Decimal(analytics_res.data['by_category']['supplier_credit']), Decimal('8000.00'))
>>>>>>> 6f155c9 (Add expense support)
