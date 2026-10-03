<<<<<<< HEAD
from django.test import TestCase

# Create your tests here.
=======
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop, RolePermission
from staff.models import Worker
from .models import Liability, LiabilityPayment


def make_worker(username, role, org=None, shop=None):
    user = User.objects.create_user(username=username, password='x')
    if org is None:
        org = Organization.objects.create(name=f'{username} Shop', owner=None, business_type='general')
    if shop is None:
        shop = Shop.objects.filter(organization=org).first() or Shop.objects.create(name=org.name, organization=org)
    worker = Worker.objects.create(user=user, full_name=username, role=role, shop=shop)
    return user, worker, org, shop


class LiabilityCapabilityTests(TestCase):
    def setUp(self):
        self.owner, self.owner_worker, self.org, self.shop = make_worker('owner1', 'owner')
        self.seller, _, _, _ = make_worker('seller1', 'seller', org=self.org, shop=self.shop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_seller_cannot_create_liability_by_default(self):
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/liabilities/', {'name': 'Rent', 'category': 'rent', 'amount': '50000'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_owner_can_create_liability(self):
        res = self.client.post('/api/liabilities/', {'name': 'Rent', 'category': 'rent', 'amount': '50000'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'unpaid')
        self.assertEqual(Decimal(res.data['outstanding']), Decimal('50000.00'))

    def test_control_center_can_grant_seller_manage_liabilities(self):
        RolePermission.objects.create(organization=self.org, role='seller', capability='manage_liabilities', allowed=True)
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/liabilities/', {'name': 'Rent', 'category': 'rent', 'amount': '50000'}, format='json')
        self.assertEqual(res.status_code, 201)


class LiabilityPartialPaymentTests(TestCase):
    """The exact scenario from the spec: 100k liability, three payments of
    30k/20k/50k, outstanding tracked correctly at every step, full history
    preserved, nothing double-counted."""

    def setUp(self):
        self.owner, self.owner_worker, self.org, self.shop = make_worker('owner2', 'owner')
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        res = self.client.post('/api/liabilities/', {
            'name': 'Staff salary', 'category': 'salary', 'amount': '100000',
        }, format='json')
        self.liability_id = res.data['id']

    def _pay(self, amount, method='cash'):
        return self.client.post('/api/liability-payments/', {
            'liability': self.liability_id, 'amount': amount, 'payment_method': method,
        }, format='json')

    def test_three_partial_payments_clear_it_exactly(self):
        r1 = self._pay('30000')
        self.assertEqual(r1.status_code, 201)
        liability = Liability.objects.get(id=self.liability_id)
        self.assertEqual(liability.status, 'partially_paid')
        self.assertEqual(liability.outstanding, Decimal('70000.00'))

        r2 = self._pay('20000')
        self.assertEqual(r2.status_code, 201)
        liability.refresh_from_db()
        self.assertEqual(liability.status, 'partially_paid')
        self.assertEqual(liability.outstanding, Decimal('50000.00'))

        r3 = self._pay('50000')
        self.assertEqual(r3.status_code, 201)
        liability.refresh_from_db()
        self.assertEqual(liability.status, 'cleared')
        self.assertEqual(liability.outstanding, Decimal('0.00'))
        self.assertIsNotNone(liability.cleared_at)

        # Full history preserved -- three separate rows, not collapsed.
        self.assertEqual(liability.payments.count(), 3)
        self.assertEqual(liability.amount, Decimal('100000.00'))  # original never mutated

    def test_cannot_overpay(self):
        self._pay('60000')
        res = self._pay('60000')  # would total 120k against a 100k liability
        self.assertEqual(res.status_code, 400)
        liability = Liability.objects.get(id=self.liability_id)
        self.assertEqual(liability.outstanding, Decimal('40000.00'))  # unaffected by the rejected attempt

    def test_deleting_a_payment_reopens_the_liability(self):
        r1 = self._pay('100000')
        liability = Liability.objects.get(id=self.liability_id)
        self.assertEqual(liability.status, 'cleared')

        payment_id = r1.data['id']
        res = self.client.delete(f'/api/liability-payments/{payment_id}/')
        self.assertEqual(res.status_code, 204)
        liability.refresh_from_db()
        self.assertEqual(liability.status, 'unpaid')
        self.assertIsNone(liability.cleared_at)

    def test_amount_cannot_be_edited_once_a_payment_exists(self):
        self._pay('10000')
        res = self.client.patch(f'/api/liabilities/{self.liability_id}/', {'amount': '200000'}, format='json')
        self.assertEqual(res.status_code, 400)


class LiabilityExpenseClassificationTests(TestCase):
    """The double-counting guard: salary/rent/utility/other payments count
    as an operating expense; loan/supplier_credit payments don't."""

    def setUp(self):
        self.owner, _, self.org, self.shop = make_worker('owner3', 'owner')
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def _liability_and_payment(self, category):
        res = self.client.post('/api/liabilities/', {'name': 'x', 'category': category, 'amount': '10000'}, format='json')
        pay_res = self.client.post('/api/liability-payments/', {
            'liability': res.data['id'], 'amount': '10000', 'payment_method': 'cash',
        }, format='json')
        return pay_res

    def test_salary_rent_utility_other_count_as_expense(self):
        for category in ('salary', 'rent', 'utility', 'other'):
            res = self._liability_and_payment(category)
            self.assertTrue(res.data['counts_as_expense'], f'{category} should count as an expense')

    def test_loan_and_supplier_credit_do_not_count_as_expense(self):
        for category in ('loan', 'supplier_credit'):
            res = self._liability_and_payment(category)
            self.assertFalse(res.data['counts_as_expense'], f'{category} should NOT count as an expense')


class LiabilityCrossBranchTests(TestCase):
    def test_cannot_pay_a_liability_from_another_branch(self):
        owner_a, _, org_a, shop_a = make_worker('ownerA', 'owner')
        owner_b, _, org_b, shop_b = make_worker('ownerB', 'owner')

        client_a = APIClient()
        client_a.force_authenticate(user=owner_a)
        res = client_a.post('/api/liabilities/', {'name': 'x', 'category': 'rent', 'amount': '5000'}, format='json')
        liability_id = res.data['id']

        client_b = APIClient()
        client_b.force_authenticate(user=owner_b)
        pay_res = client_b.post('/api/liability-payments/', {
            'liability': liability_id, 'amount': '1000', 'payment_method': 'cash',
        }, format='json')
        self.assertEqual(pay_res.status_code, 400)
>>>>>>> 6f155c9 (Add expense support)
