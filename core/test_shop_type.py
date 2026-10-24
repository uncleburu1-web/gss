"""Each branch is its own kind of shop, and a restaurant is one of the kinds (owner's decision 13).

    python manage.py test core.test_shop_type
"""
import importlib
import uuid

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditLog
from core.models import BranchModule, EmailOTP, Organization, Shop
from restaurant.tests import client_for, make_business, paid, worker


def modules_of(shop):
    return set(BranchModule.objects.filter(shop=shop, enabled=True).values_list('module', flat=True))


def sign_up(business_type, name='Mama Put'):
    tag = uuid.uuid4().hex[:6]
    r = APIClient().post('/api/auth/register/', {
        'shop_name': name, 'business_type': business_type, 'username': f'owner-{tag}',
        'email': f'{tag}@example.com', 'password': 'a-long-password'}, format='json')
    assert r.status_code == 201, r.content
    user = User.objects.get(username=f'owner-{tag}')
    code = EmailOTP.objects.filter(user=user).latest('id').code
    r = APIClient().post('/api/auth/verify-otp/', {'email': user.email, 'code': code}, format='json')
    assert r.status_code == 200, r.content
    return user, Shop.objects.get(organization__owner=user)


class SignUpTests(TestCase):
    def test_signing_up_as_a_restaurant_gives_a_restaurant_branch(self):
        owner, shop = sign_up('restaurant')
        self.assertEqual((shop.organization.business_type, shop.shop_type), ('restaurant', 'restaurant'))
        self.assertEqual(modules_of(shop), {'retail', 'restaurant'})
        me = client_for(owner).get('/api/me/').json()
        self.assertEqual(me['business_type'], 'restaurant')
        self.assertEqual(me['modules'], ['restaurant', 'retail'])
        self.assertFalse(me['service_enabled'])
        self.assertFalse(me['pharmacy_enabled'])
        self.assertEqual([c['value'] for c in me['available_categories']],
                         ['beverage', 'ingredient', 'packaging', 'kitchen_supply', 'other'])
        # The restaurant set-up works straight away, during the trial.
        self.assertEqual(client_for(owner).get('/api/restaurant/settings/').status_code, 200)
        r = client_for(owner).post('/api/restaurant/menu/categories/', {'name': 'Soups'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)

    def test_other_types_sign_up_exactly_as_before(self):
        for business_type, expected, service in (('gadgets', {'retail', 'service'}, True), ('pharmacy', {'retail'}, False),
                                                  ('clothing', {'retail'}, False), ('general', {'retail'}, False)):
            owner, shop = sign_up(business_type)
            self.assertEqual(shop.shop_type, business_type)
            self.assertEqual(modules_of(shop), expected, business_type)
            me = client_for(owner).get('/api/me/').json()
            self.assertEqual((me['business_type'], me['service_enabled'], me['pharmacy_enabled']),
                             (business_type, service, business_type == 'pharmacy'))
            self.assertEqual(client_for(owner).get('/api/restaurant/settings/').status_code, 403, business_type)

    def test_an_unknown_type_is_refused(self):
        r = APIClient().post('/api/auth/register/', {'shop_name': 'X', 'business_type': 'bakery', 'username': 'u-bakery',
                                                     'email': 'b@example.com', 'password': 'a-long-password'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('restaurant', r.json()['detail'])


class ExistingBranchesTests(TestCase):
    def test_existing_branches_take_their_business_type(self):
        """The migration: a branch with no type gets its business's sign-up type, nothing else changes."""
        org, shop, _ceo = make_business('Phones', business_type='gadgets')
        other = Shop.objects.create(name='Phones Ikeja', organization=org)
        Shop.objects.filter(pk__in=[shop.pk, other.pk]).update(shop_type='')
        before = {s.pk: modules_of(s) for s in (shop, other)}
        migration = importlib.import_module('core.migrations.0012_shop_type')
        migration.copy_business_type(django_apps, None)
        for s in (shop, other):
            s.refresh_from_db()
            self.assertEqual(s.shop_type, 'gadgets')
            self.assertEqual(modules_of(s), before[s.pk])

    def test_a_branch_created_without_a_type_takes_the_business_type(self):
        org, shop, _ceo = make_business('Health', business_type='pharmacy')
        self.assertEqual(shop.shop_type, 'pharmacy')
        self.assertEqual(Shop.objects.create(name='Legacy', organization=None).shop_type, 'general')


class BranchTypeTests(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo = make_business('Chidi', business_type='gadgets')
        paid(self.org, 'retail_restaurant')

    def create(self, **body):
        return client_for(self.ceo).post('/api/branches/', {'name': f'Branch {uuid.uuid4().hex[:4]}', **body}, format='json')

    def test_ceo_adds_a_restaurant_beside_a_gadget_shop(self):
        r = self.create(shop_type='restaurant')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()['shop_type'], r.json()['shop_type_label'].split(' (')[0]), ('restaurant', 'Restaurant & food'))
        branch = Shop.objects.get(pk=r.json()['id'])
        self.assertEqual(modules_of(branch), {'retail', 'restaurant'})
        self.assertEqual(modules_of(self.shop), {'retail', 'service'}, 'the gadget shop is untouched')
        # Restaurant set-up for the new branch, not for the gadget shop.
        self.assertEqual(client_for(self.ceo, branch).get('/api/restaurant/settings/').status_code, 200)
        self.assertEqual(client_for(self.ceo, self.shop).get('/api/restaurant/settings/').status_code, 403)
        me = client_for(self.ceo).get('/api/me/').json()
        self.assertEqual(me['branch_types'], {str(self.shop.id): 'gadgets', str(branch.id): 'restaurant'})
        self.assertEqual(me['business_type'], 'gadgets', "/me's own fields stay the CEO's own branch")
        types = {t['value']: t for t in me['shop_types']}
        self.assertEqual(list(types), ['gadgets', 'clothing', 'pharmacy', 'restaurant', 'general'])
        self.assertEqual(types['restaurant']['modules'], ['retail', 'restaurant'])
        self.assertIn('ingredient', [c['value'] for c in types['restaurant']['categories']])

    def test_a_branch_without_a_type_takes_the_sign_up_type(self):
        r = self.create()
        self.assertEqual((r.status_code, r.json()['shop_type']), (201, 'gadgets'))
        self.assertEqual(modules_of(Shop.objects.get(pk=r.json()['id'])), {'retail', 'service'})

    def test_a_restaurant_branch_needs_a_package_with_the_restaurant(self):
        paid(self.org, 'retail')
        r = self.create(shop_type='restaurant')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'not_in_package'))
        self.assertEqual(self.org.branches.count(), 1, 'nothing was created')
        self.assertEqual(self.create(shop_type='pharmacy').status_code, 201)

    def test_unknown_type_is_refused(self):
        self.assertEqual(self.create(shop_type='bakery').status_code, 400)

    def test_changing_a_branch_into_a_restaurant_and_back(self):
        url = f'/api/branches/{self.shop.id}/'
        r = client_for(self.ceo).patch(url, {'shop_type': 'restaurant'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.shop_type, 'restaurant')
        self.assertEqual(modules_of(self.shop), {'retail', 'restaurant'}, 'repairs off, restaurant on')
        log = AuditLog.objects.get(action='branch.type_changed', shop=self.shop)
        self.assertEqual((log.data['from'], log.data['to'], log.data['modules']), ('gadgets', 'restaurant', {'restaurant': True, 'service': False}))
        client_for(self.ceo).post('/api/restaurant/menu/categories/', {'name': 'Soups'}, format='json')

        r = client_for(self.ceo).patch(url, {'shop_type': 'gadgets'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(modules_of(self.shop), {'retail', 'service'})
        from restaurant.models import MenuCategory
        self.assertTrue(MenuCategory.objects.filter(shop=self.shop, name='Soups').exists(), 'switching off keeps what was recorded')

    def test_editing_other_fields_leaves_the_modules_alone(self):
        BranchModule.objects.update_or_create(shop=self.shop, module='restaurant', defaults={'enabled': True})
        r = client_for(self.ceo).patch(f'/api/branches/{self.shop.id}/', {'phone': '0800', 'shop_type': 'gadgets'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(modules_of(self.shop), {'retail', 'service', 'restaurant'})
        self.assertFalse(AuditLog.objects.filter(action='branch.type_changed').exists())

    def test_changing_into_a_restaurant_needs_the_package(self):
        paid(self.org, 'retail')
        r = client_for(self.ceo).patch(f'/api/branches/{self.shop.id}/', {'shop_type': 'restaurant'}, format='json')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'not_in_package'))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.shop_type, 'gadgets')
        # Leaving a type is always allowed.
        self.assertEqual(client_for(self.ceo).patch(f'/api/branches/{self.shop.id}/', {'shop_type': 'general'}, format='json').status_code, 200)
        self.assertEqual(modules_of(self.shop), {'retail'})

    def test_only_the_ceo_changes_a_branch_type(self):
        for role in ('branch_manager', 'seller'):
            r = client_for(worker(self.shop, role)).patch(f'/api/branches/{self.shop.id}/', {'shop_type': 'restaurant'}, format='json')
            self.assertEqual(r.status_code, 403, role)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.shop_type, 'gadgets')

    def test_another_business_cannot_change_a_branch(self):
        _org, _shop, other_ceo = make_business('Other')
        r = client_for(other_ceo).patch(f'/api/branches/{self.shop.id}/', {'shop_type': 'restaurant'}, format='json')
        self.assertEqual(r.status_code, 404)

    def test_restaurant_branch_staff_see_their_own_branch_type(self):
        r = self.create(shop_type='restaurant')
        branch = Shop.objects.get(pk=r.json()['id'])
        manager = worker(branch, 'branch_manager')
        me = client_for(manager).get('/api/me/').json()
        self.assertEqual((me['business_type'], me['organization_business_type'], me['service_enabled']), ('restaurant', 'gadgets', False))
        self.assertEqual(me['branch_types'], {str(branch.id): 'restaurant'}, 'a branch manager sees only their branch')
        self.assertIn('ingredient', [c['value'] for c in me['available_categories']])


class RolesTests(TestCase):
    """A restaurant hires waiters, cashiers and kitchen staff; a shop hires sellers and the like."""

    def setUp(self):
        self.org, self.shop, self.ceo = make_business('Chidi', business_type='gadgets')
        paid(self.org, 'retail_restaurant')
        r = client_for(self.ceo).post('/api/branches/', {'name': 'Chidi Kitchen', 'shop_type': 'restaurant'}, format='json')
        self.kitchen = Shop.objects.get(pk=r.json()['id'])

    def add(self, branch, role, **extra):
        return client_for(self.ceo, branch).post('/api/workers/', {'full_name': f'{role} {uuid.uuid4().hex[:4]}', 'role': role, **extra}, format='json')

    def test_me_lists_the_roles_each_kind_offers(self):
        types = {t['value']: t for t in client_for(self.ceo).get('/api/me/').json()['shop_types']}
        self.assertEqual([r['value'] for r in types['restaurant']['roles']], ['waiter', 'cashier', 'kitchen', 'other'])
        self.assertEqual([r['value'] for r in types['gadgets']['roles']], ['seller', 'reception', 'technician', 'attendant', 'other'])
        self.assertEqual(types['restaurant']['roles'][0]['label'], 'Waiter (restaurant)')

    def test_restaurant_staff_are_added_to_the_restaurant(self):
        for role in ('waiter', 'cashier', 'kitchen'):
            r = self.add(self.kitchen, role)
            self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.kitchen.workers.filter(role__in=['waiter', 'cashier', 'kitchen']).count(), 3)

    def test_a_shop_cannot_take_a_waiter(self):
        r = self.add(self.shop, 'waiter')
        self.assertEqual(r.status_code, 400)
        self.assertIn('restaurant branch', r.json()['role'][0])
        self.assertEqual(self.add(self.shop, 'seller').status_code, 201, 'shop roles as before')

    def test_changing_a_shop_worker_into_a_waiter_is_refused_but_other_edits_are_not(self):
        seller = self.add(self.shop, 'seller').json()
        r = client_for(self.ceo, self.shop).patch(f"/api/workers/{seller['id']}/", {'role': 'waiter'}, format='json')
        self.assertEqual(r.status_code, 400)
        # A waiter whose branch stopped being a restaurant can still be edited (role unchanged).
        waiter = self.add(self.kitchen, 'waiter').json()
        client_for(self.ceo).patch(f'/api/branches/{self.kitchen.id}/', {'shop_type': 'general'}, format='json')
        r = client_for(self.ceo, self.kitchen).patch(f"/api/workers/{waiter['id']}/", {'phone': '0800'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)


class PlanTests(TestCase):
    """Owner's decision (23 Oct 2026): until the restaurant add-on price is set, every plan includes it."""

    def test_a_paying_business_without_a_package_can_run_a_restaurant(self):
        from django.utils import timezone
        from datetime import timedelta
        from subscriptions.models import Subscription, get_or_create_subscription
        org, shop, ceo = make_business('Iya Basira', business_type='general')
        sub = get_or_create_subscription(org)
        Subscription.objects.filter(pk=sub.pk).update(status='active', package=None, current_period_end=timezone.now() + timedelta(days=30))
        r = client_for(ceo).patch(f'/api/branches/{shop.id}/', {'shop_type': 'restaurant'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(client_for(ceo).post('/api/restaurant/menu/categories/', {'name': 'Soups'}, format='json').status_code, 201)
        # A business put on an explicit Retail package in the admin is still held to it.
        paid(org, 'retail')
        r = client_for(ceo).post('/api/branches/', {'name': 'Second', 'shop_type': 'restaurant'}, format='json')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'not_in_package'))

