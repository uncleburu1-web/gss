"""
REST-level check for per-branch barcode uniqueness: a duplicate barcode in one branch is a friendly 400
(never a 500), and a different shop can still use the same code. The model-level cases are already
covered by inventory/tests.py.

    python manage.py test inventory.test_barcode_api
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop
from staff.models import Worker


def make_shop(name):
    org = Organization.objects.create(name=name, owner=None, business_type='general')
    return Shop.objects.create(name=name, organization=org)


class BarcodeApiTests(TestCase):
    """The REST layer must turn a duplicate into a friendly 400, not a 500."""

    def setUp(self):
        self.shop_a = make_shop('Shop A')
        self.shop_b = make_shop('Shop B')
        self.owner_a = self._owner('owner_a', self.shop_a)
        self.owner_b = self._owner('owner_b', self.shop_b)

    @staticmethod
    def _owner(username, shop):
        user = User.objects.create_user(username=username, password='x')
        Worker.objects.create(user=user, full_name=username, role='owner', shop=shop)
        return user

    @staticmethod
    def _client(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_duplicate_in_the_same_shop_is_a_400_and_other_shops_are_unaffected(self):
        body = {'name': 'Cola', 'barcode': '5000112637922', 'unit': 'PIECE', 'category': 'other', 'sell_price': '100.00'}

        first = self._client(self.owner_a).post('/api/inventory/items/', body, format='json')
        again = self._client(self.owner_a).post('/api/inventory/items/', body, format='json')
        other_shop = self._client(self.owner_b).post('/api/inventory/items/', body, format='json')

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(again.status_code, 400, again.content)
        self.assertIn('barcode', again.json())
        self.assertEqual(other_shop.status_code, 201, other_shop.content)
