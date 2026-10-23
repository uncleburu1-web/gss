"""
Stage 0 regression tests for device pairing / heartbeat (audit finding D-14).

    python manage.py test devices
"""
import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop
from staff.models import Worker
from .models import Device

HEARTBEAT_URL = '/api/devices/heartbeat/'
LOGIN_URL = '/api/auth/login/'


def make_shop_user(username, shop_name):
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name=shop_name, owner=None, business_type='general')
    shop = Shop.objects.create(name=shop_name, organization=org)
    Worker.objects.create(user=user, full_name=username, role='seller', shop=shop)
    return user, shop


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class HeartbeatTests(TestCase):
    def setUp(self):
        self.user_a, self.shop_a = make_shop_user('alice', 'Shop A')
        self.user_b, self.shop_b = make_shop_user('bob', 'Shop B')

    def _beat(self, client, device_id, **extra):
        body = {'device_id': str(device_id), 'device_type': 'desktop', 'name': 'till-1', 'app_version': '1.2.3'}
        body.update(extra)
        return client.post(HEARTBEAT_URL, body, format='json')

    def test_a_first_heartbeat_registers_the_device(self):
        device_id = uuid.uuid4()
        response = self._beat(client_for(self.user_a), device_id)
        self.assertEqual(response.status_code, 200)
        device = Device.objects.get(id=device_id)
        self.assertEqual(device.shop, self.shop_a)
        self.assertEqual(device.app_version, '1.2.3')

    def test_a_removed_desktop_is_not_resurrected_by_its_next_heartbeat(self):
        device = Device.objects.create(id=uuid.uuid4(), shop=self.shop_a, device_type='desktop', is_deleted=True)

        response = self._beat(client_for(self.user_a), device.id)

        self.assertEqual(response.status_code, 410)
        device.refresh_from_db()
        self.assertTrue(device.is_deleted)

    def test_another_shops_user_cannot_write_to_a_device(self):
        device = Device.objects.create(id=uuid.uuid4(), shop=self.shop_a, device_type='desktop', name='original')

        response = self._beat(client_for(self.user_b), device.id, name='hijacked')

        self.assertEqual(response.status_code, 403)
        device.refresh_from_db()
        self.assertEqual(device.name, 'original')
        self.assertEqual(device.shop, self.shop_a)


class RepairAfterRemovalTests(TestCase):
    """Removing a device only soft-deletes its row. The machine's next login
    must re-pair it, not crash on the dead row's primary key (this used to be
    hidden by the heartbeat resurrecting the row)."""

    def test_login_after_the_device_was_removed_pairs_it_again(self):
        user, shop = make_shop_user('erin', 'Shop E')
        device = Device.objects.create(id=uuid.uuid4(), shop=shop, device_type='desktop', is_deleted=True)

        response = APIClient().post(LOGIN_URL, {
            'username': 'erin', 'password': 'x',
            'device_id': str(device.id), 'device_type': 'desktop',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        device.refresh_from_db()
        self.assertFalse(device.is_deleted)
        self.assertEqual(device.shop, shop)


class MultiBranchCeoDeviceListTests(TestCase):
    """A CEO with several branches sees one branch's tills at a time, the one named by X-Branch-ID
    (the web's branch switcher). Before, the list ignored the header and refused every such CEO."""

    def setUp(self):
        self.ceo = User.objects.create_user(username='ceo-multi', password='x')
        org = Organization.objects.create(name='Two Shops', owner=self.ceo, business_type='general')
        self.ikeja = Shop.objects.create(name='Ikeja', organization=org)
        self.lekki = Shop.objects.create(name='Lekki', organization=org)
        Worker.objects.create(user=self.ceo, full_name='Chief', role='owner', shop=self.ikeja)
        self.till_ikeja = Device.objects.create(shop=self.ikeja, device_type='desktop', name='Ikeja till')
        self.till_lekki = Device.objects.create(shop=self.lekki, device_type='desktop', name='Lekki till')
        _, self.other_shop = make_shop_user('stranger', 'Elsewhere')

    def test_the_branch_named_in_the_header_is_listed(self):
        client = client_for(self.ceo)
        r = client.get('/api/devices/', HTTP_X_BRANCH_ID=str(self.lekki.id))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([d['name'] for d in r.json()], ['Lekki till'])
        r = client.get('/api/devices/', HTTP_X_BRANCH_ID=str(self.ikeja.id))
        self.assertEqual([d['name'] for d in r.json()], ['Ikeja till'])

    def test_another_business_branch_is_never_listed(self):
        r = client_for(self.ceo).get('/api/devices/', HTTP_X_BRANCH_ID=str(self.other_shop.id))
        self.assertIn(r.status_code, (403, 404))

    def test_removing_a_till_in_the_branch_being_viewed(self):
        client = client_for(self.ceo)
        r = client.delete(f'/api/devices/{self.till_lekki.id}/', HTTP_X_BRANCH_ID=str(self.lekki.id))
        self.assertIn(r.status_code, (200, 204), r.content)
        self.till_lekki.refresh_from_db()
        self.assertTrue(self.till_lekki.is_deleted)
        r = client.delete(f'/api/devices/{self.till_ikeja.id}/', HTTP_X_BRANCH_ID=str(self.lekki.id))
        self.assertEqual(r.status_code, 404, 'a till of another branch is not found in this one')
