from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop, RolePermission
from staff.models import Worker
from .models import Expense, ExpenseCategory


def make_worker(username, role, org=None, shop=None):
    user = User.objects.create_user(username=username, password='x')
    if org is None:
        org = Organization.objects.create(name=f'{username} Shop', owner=None, business_type='general')
    if shop is None:
        shop = Shop.objects.filter(organization=org).first() or Shop.objects.create(name=org.name, organization=org)
    worker = Worker.objects.create(user=user, full_name=username, role=role, shop=shop)
    return user, worker, org, shop


class ExpenseCategoryLazySeedTests(TestCase):
    def test_categories_seeded_on_first_fetch(self):
        owner, _, org, shop = make_worker('owner1', 'owner')
        self.assertEqual(ExpenseCategory.objects.filter(shop=shop).count(), 0)

        client = APIClient()
        client.force_authenticate(user=owner)
        res = client.get('/api/expense-categories/')
        self.assertEqual(res.status_code, 200)
        rows = res.data['results'] if 'results' in res.data else res.data
        self.assertEqual(len(rows), 14)  # DEFAULT_EXPENSE_CATEGORIES
        self.assertTrue(all(r['is_default'] for r in rows))

    def test_seeding_only_happens_once(self):
        owner, _, org, shop = make_worker('owner2', 'owner')
        client = APIClient()
        client.force_authenticate(user=owner)
        client.get('/api/expense-categories/')  # triggers seeding
        client.post('/api/expense-categories/', {'name': 'Custom One'}, format='json')
        res = client.get('/api/expense-categories/')
        rows = res.data['results'] if 'results' in res.data else res.data
        self.assertEqual(len(rows), 15)  # 14 defaults + 1 custom, not re-seeded to 28


class ExpenseCapabilityTests(TestCase):
    def setUp(self):
        self.owner, _, self.org, self.shop = make_worker('owner3', 'owner')
        self.seller, self.seller_worker, _, _ = make_worker('seller1', 'seller', org=self.org, shop=self.shop)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        cat_res = self.client.get('/api/expense-categories/')  # seeds categories
        rows = cat_res.data['results'] if 'results' in cat_res.data else cat_res.data
        self.fuel_category_id = next(r['id'] for r in rows if r['name'] == 'Fuel')

    def test_seller_cannot_record_expense_by_default(self):
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/expenses/', {
            'category': self.fuel_category_id, 'amount': '5000', 'payment_method': 'cash', 'date': '2026-09-29',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_owner_can_record_expense(self):
        res = self.client.post('/api/expenses/', {
            'category': self.fuel_category_id, 'amount': '5000', 'payment_method': 'cash', 'date': '2026-09-29',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        expense = Expense.objects.get(id=res.data['id'])
        self.assertEqual(expense.recorded_by_id, self.owner.worker.id)
        self.assertEqual(str(expense.amount), '5000.00')

    def test_control_center_can_grant_seller_manage_expenses(self):
        RolePermission.objects.create(organization=self.org, role='seller', capability='manage_expenses', allowed=True)
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/expenses/', {
            'category': self.fuel_category_id, 'amount': '5000', 'payment_method': 'cash', 'date': '2026-09-29',
        }, format='json')
        self.assertEqual(res.status_code, 201)

    def test_category_from_another_branch_is_rejected(self):
        _, _, _, other_shop = make_worker('owner4', 'owner')
        other_cat = ExpenseCategory.objects.create(shop=other_shop, name='Foreign category')
        res = self.client.post('/api/expenses/', {
            'category': str(other_cat.id), 'amount': '1000', 'payment_method': 'cash', 'date': '2026-09-29',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_category_in_use_cannot_be_hard_deleted_but_can_be_soft_deleted(self):
        # PROTECT stops a real DB-level delete (e.g. from admin) while an
        # expense references it...
        from django.db.models import ProtectedError
        cat = ExpenseCategory.objects.get(id=self.fuel_category_id)
        Expense.objects.create(shop=self.shop, category=cat, amount=1000, date='2026-09-29')
        with self.assertRaises(ProtectedError):
            cat.delete()
        # ...but the normal DELETE endpoint (soft-delete) still works fine,
        # and the historical expense keeps reading the category correctly.
        res = self.client.delete(f'/api/expense-categories/{cat.id}/')
        self.assertEqual(res.status_code, 204)
        cat.refresh_from_db()
        self.assertTrue(cat.is_deleted)
        expense = Expense.objects.first()
        self.assertEqual(expense.category.name, 'Fuel')
