"""Restaurant configuration, branch modules, packages and kitchen routing.

    python manage.py test restaurant
"""
import datetime
import json
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditLog
from core.models import BranchModule, Organization, Shop
from devices.models import Device
from staff.models import Worker
from subscriptions.models import Package, Subscription, get_or_create_subscription

from . import routing
from .models import KitchenStation, MenuCategory, MenuItem, TaxRule

VECTORS = json.loads((Path(__file__).parent / 'routing_vectors.json').read_text())


def client_for(user, branch=None):
    client = APIClient()
    client.force_authenticate(user=user)
    if branch is not None:
        client.credentials(HTTP_X_BRANCH_ID=str(branch.id))
    return client


def make_business(name='Mama Put', business_type='general'):
    ceo = User.objects.create_user(username=f'ceo-{uuid.uuid4().hex[:6]}', password='x')
    org = Organization.objects.create(name=name, owner=ceo, business_type=business_type)
    shop = Shop.objects.create(name=f'{name} Wuse', organization=org)
    Worker.objects.create(user=ceo, full_name='Chief', role='owner', shop=shop)
    return org, shop, ceo


def worker(shop, role):
    user = User.objects.create_user(username=f'{role}-{uuid.uuid4().hex[:6]}', password='x')
    Worker.objects.create(user=user, full_name=role.title(), role=role, shop=shop)
    return user


def paid(org, package_code):
    sub = get_or_create_subscription(org)
    Subscription.objects.filter(pk=sub.pk).update(status='active', current_period_end=timezone.now() + timedelta(days=30),
                                                  package=Package.objects.get(code=package_code))


def turn_on_restaurant(shop):
    BranchModule.objects.update_or_create(shop=shop, module='restaurant', defaults={'enabled': True})


class BranchModuleTests(TestCase):
    def test_every_new_branch_starts_as_a_supermarket_branch(self):
        _org, shop, _ceo = make_business()
        self.assertEqual(set(BranchModule.objects.filter(shop=shop).values_list('module', flat=True)), {'retail'})
        _org, gadgets, _ceo = make_business('Phones', business_type='gadgets')
        self.assertEqual(set(BranchModule.objects.filter(shop=gadgets).values_list('module', flat=True)), {'retail', 'service'})

    def test_ceo_switches_restaurant_on_during_the_trial(self):
        org, shop, ceo = make_business()
        r = client_for(ceo).put(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['modules']['restaurant'])
        self.assertTrue(r.json()['modules']['retail'], 'switching restaurant on leaves retail alone')
        self.assertTrue(AuditLog.objects.filter(action='branch.modules_changed', shop=shop).exists())

    def test_package_without_restaurant_cannot_switch_it_on(self):
        org, shop, ceo = make_business()
        paid(org, 'retail')
        r = client_for(ceo).put(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'not_in_package'))
        paid(org, 'retail_restaurant')
        r = client_for(ceo).put(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_downgrading_the_package_never_switches_a_running_restaurant_off(self):
        org, shop, ceo = make_business()
        turn_on_restaurant(shop)
        paid(org, 'retail')
        self.assertEqual(client_for(ceo).get('/api/restaurant/stations/').status_code, 200)

    def test_only_ceo_or_that_branchs_owner_can_change_modules(self):
        org, shop, ceo = make_business()
        seller = worker(shop, 'seller')
        r = client_for(seller).put(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
        self.assertEqual(r.status_code, 403)
        manager = worker(shop, 'branch_manager')
        r = client_for(manager).put(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_another_business_cannot_see_or_change_a_branch(self):
        _org, shop, _ceo = make_business()
        _org2, _shop2, other_ceo = make_business('Other')
        for method in ('get', 'put'):
            r = getattr(client_for(other_ceo), method)(f'/api/branches/{shop.id}/modules/', {'modules': {'restaurant': True}}, format='json')
            self.assertEqual(r.status_code, 404)
        self.assertEqual(client_for(other_ceo).get('/api/branches/not-a-uuid/modules/').status_code, 404)

    def test_me_reports_modules_and_features(self):
        org, shop, ceo = make_business()
        turn_on_restaurant(shop)
        me = client_for(ceo).get('/api/me/').json()
        self.assertEqual(me['modules'], ['restaurant', 'retail'])
        self.assertEqual(me['branch_modules'][str(shop.id)], ['restaurant', 'retail'])
        self.assertIn('restaurant.kitchen', me['features'], 'the trial includes every feature')


class ConfigAccessTests(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo = make_business()
        turn_on_restaurant(self.shop)
        self.owner = client_for(self.ceo)

    def test_restaurant_api_needs_the_module_on_the_branch(self):
        _org, plain, plain_ceo = make_business('Supermarket')
        r = client_for(plain_ceo).get('/api/restaurant/stations/')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'module_disabled'))

    def test_owner_sets_up_staff_read_but_cannot_change(self):
        station = self.owner.post('/api/restaurant/stations/', {'name': 'Kitchen'}, format='json')
        self.assertEqual(station.status_code, 201, station.content)
        for role in ('waiter', 'cashier', 'kitchen', 'seller'):
            staff = client_for(worker(self.shop, role))
            self.assertEqual(staff.get('/api/restaurant/stations/').status_code, 200, role)
            self.assertEqual(staff.post('/api/restaurant/stations/', {'name': 'Grill'}, format='json').status_code, 403, role)
        self.assertTrue(AuditLog.objects.filter(action='restaurant.config.changed', shop=self.shop).exists())

    def test_saving_configuration_needs_a_restaurant_package(self):
        paid(self.org, 'retail')
        r = self.owner.post('/api/restaurant/stations/', {'name': 'Kitchen'}, format='json')
        self.assertEqual((r.status_code, r.json()['code']), (403, 'not_in_package'))

    def test_another_business_never_sees_or_touches_this_menu(self):
        station = KitchenStation.objects.create(shop=self.shop, name='Kitchen')
        _org2, shop2, ceo2 = make_business('Other')
        turn_on_restaurant(shop2)
        other = client_for(ceo2)
        self.assertEqual(other.get('/api/restaurant/stations/').json()['results'], [])
        self.assertEqual(other.get(f'/api/restaurant/stations/{station.id}/').status_code, 404)
        self.assertEqual(other.patch(f'/api/restaurant/stations/{station.id}/', {'name': 'Mine'}, format='json').status_code, 404)
        # Nor point its own menu at this branch's station.
        r = other.post('/api/restaurant/menu/categories/', {'name': 'Rice', 'station': str(station.id)}, format='json')
        self.assertEqual(r.status_code, 400)
        # X-Branch-ID naming someone else's branch is ignored, never trusted.
        sneaky = client_for(ceo2, branch=self.shop)
        self.assertEqual(sneaky.get('/api/restaurant/stations/').json()['results'], [])

    def test_a_ceo_with_two_branches_must_pick_one(self):
        second = Shop.objects.create(name='Lekki', organization=self.org)
        turn_on_restaurant(second)
        self.assertEqual(self.owner.get('/api/restaurant/stations/').status_code, 403)
        self.assertEqual(client_for(self.ceo, branch=second).get('/api/restaurant/stations/').status_code, 200)

    def test_validation(self):
        r = self.owner.post('/api/restaurant/stations/', {'name': 'Ghost', 'prints': False, 'shows_on_screen': False}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.owner.post('/api/restaurant/menu/modifier-groups/', {'name': 'Size', 'min_select': 2, 'max_select': 1}, format='json')
        self.assertEqual(r.status_code, 400)
        cat = MenuCategory.objects.create(shop=self.shop, name='Rice')
        r = self.owner.post('/api/restaurant/menu/items/', {'category': str(cat.id), 'name': 'Jollof', 'price': '-1'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_deleted_names_can_be_reused(self):
        first = self.owner.post('/api/restaurant/stations/', {'name': 'Grill'}, format='json').json()
        self.assertEqual(self.owner.delete(f"/api/restaurant/stations/{first['id']}/").status_code, 204)
        self.assertEqual(self.owner.post('/api/restaurant/stations/', {'name': 'Grill'}, format='json').status_code, 201)
        self.assertEqual(self.owner.post('/api/restaurant/stations/', {'name': 'Grill'}, format='json').status_code, 400)

    def test_settings_and_hub(self):
        r = self.owner.put('/api/restaurant/settings/', {'service_charge_rate': '10', 'service_charge_types': ['dine_in', 'bogus']}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.owner.put('/api/restaurant/settings/', {'service_charge_rate': '10', 'service_charge_types': ['dine_in']}, format='json')
        self.assertEqual((r.status_code, r.json()['service_charge_rate']), (200, '10.0000'))
        till = Device.objects.create(shop=self.shop, device_type='desktop', name='Counter')
        phone = Device.objects.create(shop=self.shop, device_type='android')
        _org2, shop2, _c = make_business('Other')
        foreign = Device.objects.create(shop=shop2, device_type='desktop')
        self.assertEqual(self.owner.put('/api/restaurant/settings/hub/', {'device_id': str(foreign.id)}, format='json').status_code, 404)
        self.assertEqual(self.owner.put('/api/restaurant/settings/hub/', {'device_id': str(phone.id)}, format='json').status_code, 400)
        self.assertEqual(self.owner.put('/api/restaurant/settings/hub/', {'device_id': 'nonsense'}, format='json').status_code, 404)
        r = self.owner.put('/api/restaurant/settings/hub/', {'device_id': str(till.id)}, format='json')
        self.assertEqual((r.status_code, r.json()['hub_device']), (200, str(till.id)))


class TaxTests(TestCase):
    def setUp(self):
        _org, self.shop, ceo = make_business()
        turn_on_restaurant(self.shop)
        self.owner = client_for(ceo)

    def rule(self, name, rate, classes=(), start='2026-01-01'):
        r = self.owner.post('/api/restaurant/tax-rules/', {'name': name, 'rate': rate, 'tax_classes': list(classes), 'valid_from': start}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def activate(self, ids, **extra):
        body = {'rule_ids': ids, 'state': 'Lagos', 'vat_registered': True, 'treatment_confirmed': True, **extra}
        return self.owner.post('/api/restaurant/tax-rules/activate/', body, format='json')

    def test_a_new_tax_is_inactive_and_cannot_be_switched_on_by_editing(self):
        vat = self.rule('VAT', '7.5')
        self.assertFalse(vat['is_active'])
        r = self.owner.patch(f"/api/restaurant/tax-rules/{vat['id']}/", {'is_active': True}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_activation_needs_the_checks(self):
        vat = self.rule('VAT', '7.5')
        r = self.owner.post('/api/restaurant/tax-rules/activate/', {'rule_ids': [vat['id']], 'state': 'Lagos', 'vat_registered': True}, format='json')
        self.assertEqual(r.json()['code'], 'confirm_treatment')
        self.assertEqual(self.activate([vat['id']], state='').json()['code'], 'state_required')
        self.assertEqual(self.activate([vat['id']], vat_registered=None).json()['code'], 'vat_registration_required')
        self.assertEqual(self.activate([vat['id'], 'junk']).status_code, 404)
        r = self.activate([vat['id']])
        self.assertEqual(r.status_code, 200, r.content)
        settings = self.owner.get('/api/restaurant/settings/').json()
        self.assertEqual((settings['tax_state'], settings['vat_registered']), ('Lagos', True))
        self.assertTrue(AuditLog.objects.filter(action='restaurant.tax_activated', shop=self.shop).exists())

    def test_overlapping_taxes_are_never_stacked_without_confirmation(self):
        vat = self.rule('VAT', '7.5')
        lagos = self.rule('Lagos consumption tax', '5')
        self.assertEqual(self.activate([vat['id']]).status_code, 200)
        r = self.activate([lagos['id']])
        self.assertEqual((r.status_code, r.json()['code']), (409, 'overlapping_taxes'))
        self.assertFalse(TaxRule.objects.get(id=lagos['id']).is_active)
        r = self.activate([lagos['id']], overlap_confirmed=True)
        self.assertEqual((r.status_code, r.json()['overlap_confirmed']), (200, True))

    def test_taxes_on_different_items_do_not_clash(self):
        food = self.rule('Food levy', '2', classes=['food'])
        drink = self.rule('Drink levy', '3', classes=['drink'])
        self.assertEqual(self.activate([food['id'], drink['id']]).status_code, 200)
        expired = TaxRule.objects.create(shop=self.shop, name='Old', rate=1, valid_from=datetime.date(2025, 1, 1), valid_to=datetime.date(2025, 12, 31))
        self.assertEqual(self.activate([str(expired.id)]).status_code, 200, 'a rule that ended before the others began does not overlap')

    def test_an_active_tax_keeps_its_rate_but_can_be_switched_off(self):
        vat = self.rule('VAT', '7.5')
        self.activate([vat['id']])
        r = self.owner.patch(f"/api/restaurant/tax-rules/{vat['id']}/", {'rate': '10'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('active_rule_locked', str(r.content))
        r = self.owner.patch(f"/api/restaurant/tax-rules/{vat['id']}/", {'is_active': False}, format='json')
        self.assertEqual((r.status_code, r.json()['is_active']), (200, False))

    def test_end_date_must_follow_start(self):
        r = self.owner.post('/api/restaurant/tax-rules/', {'name': 'VAT', 'rate': '7.5', 'valid_from': '2026-02-01', 'valid_to': '2026-01-01'}, format='json')
        self.assertEqual(r.status_code, 400)


class SyncPullTests(TestCase):
    def setUp(self):
        _org, self.shop, self.ceo = make_business()
        KitchenStation.objects.create(shop=self.shop, name='Kitchen')

    def pull(self, types=None):
        url = '/api/sync/pull/' + (f'?entity_types={types}' if types else '')
        return [op['entity_type'] for op in client_for(self.ceo).get(url).json()['operations']]

    def test_supermarket_tills_and_older_versions_never_get_restaurant_data(self):
        turn_on_restaurant(self.shop)
        self.assertNotIn('kitchen_station', self.pull())

    def test_a_restaurant_branch_gets_it_when_asked(self):
        self.assertNotIn('kitchen_station', self.pull('kitchen_station'), 'not a restaurant branch yet')
        turn_on_restaurant(self.shop)
        self.assertEqual(self.pull('kitchen_station'), ['kitchen_station'])


class RoutingTests(TestCase):
    def test_shared_vectors(self):
        for case in VECTORS['station_for']:
            self.assertEqual(routing.station_for(case['item'], case['category'], case['active']), case['expect'], case['name'])
        for case in VECTORS['route']:
            self.assertEqual(routing.route(case['lines']), case['expect'], case['name'])

    def test_preview_uses_the_menu_set_up(self):
        _org, shop, ceo = make_business()
        turn_on_restaurant(shop)
        kitchen = KitchenStation.objects.create(shop=shop, name='Kitchen')
        bar = KitchenStation.objects.create(shop=shop, name='Bar')
        grill = KitchenStation.objects.create(shop=shop, name='Grill', is_active=False)
        mains = MenuCategory.objects.create(shop=shop, name='Mains', station=kitchen)
        drinks = MenuCategory.objects.create(shop=shop, name='Drinks', station=bar)
        pizza = MenuItem.objects.create(shop=shop, category=mains, name='Pizza', price=5000)
        suya = MenuItem.objects.create(shop=shop, category=mains, name='Suya', price=2000, station=grill)
        coke = MenuItem.objects.create(shop=shop, category=drinks, name='Soft drink', price=500)
        water = MenuItem.objects.create(shop=shop, category=MenuCategory.objects.create(shop=shop, name='Water'), name='Water', price=300)
        r = client_for(ceo).post('/api/restaurant/routing/preview/', {'items': [
            {'menu_item_id': str(pizza.id)}, {'menu_item_id': str(coke.id)}, {'menu_item_id': str(suya.id)}, {'menu_item_id': str(water.id)}]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([(t['station_name'], t['item_names']) for t in body['tickets']],
                         [('Kitchen', ['Pizza', 'Suya']), ('Bar', ['Soft drink'])],
                         'the grill is switched off, so suya falls back to its category (kitchen)')
        self.assertEqual(body['unrouted_names'], ['Water'])
        _o, _s, stranger = make_business('Other')
        turn_on_restaurant(_s)
        r = client_for(stranger).post('/api/restaurant/routing/preview/', {'items': [{'menu_item_id': str(pizza.id)}]}, format='json')
        self.assertEqual(r.status_code, 404, "another business's menu items do not exist for it")


class RoleTests(TestCase):
    def test_restaurant_roles_get_sensible_defaults(self):
        from core.capabilities import has_capability
        _org, shop, _ceo = make_business()
        self.assertTrue(has_capability(worker(shop, 'waiter'), 'send_to_kitchen'))
        self.assertFalse(has_capability(worker(shop, 'waiter'), 'take_payment'))
        self.assertTrue(has_capability(worker(shop, 'cashier'), 'take_payment'))
        self.assertFalse(has_capability(worker(shop, 'kitchen'), 'take_orders'))
        self.assertFalse(has_capability(worker(shop, 'cashier'), 'void_sent_item'), 'voids are off by default')


class BillingTests(TestCase):
    def test_shared_bill_cases(self):
        from .billing import compute_bill_or_error
        cases = json.loads((Path(__file__).parent / 'billing_vectors.json').read_text())['cases']
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            self.assertEqual(compute_bill_or_error(case['input']), case['expect'], case['name'])
