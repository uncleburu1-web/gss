"""
Stage 0 regression tests for barcode scoping (audit finding D-08):
a barcode identifies a PRODUCT, so different shops must be free to stock the
same EAN/UPC.

    python manage.py test inventory
"""
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop
from staff.models import Worker
from .models import InventoryItem


def make_shop(name):
    org = Organization.objects.create(name=name, owner=None, business_type='general')
    return Shop.objects.create(name=name, organization=org)


class BarcodeConstraintTests(TestCase):
    def setUp(self):
        self.shop_a = make_shop('Shop A')
        self.shop_b = make_shop('Shop B')

    def test_two_shops_can_stock_the_same_barcode(self):
        InventoryItem.objects.create(shop=self.shop_a, name='Cola', barcode='5000112637922')
        InventoryItem.objects.create(shop=self.shop_b, name='Cola', barcode='5000112637922')
        self.assertEqual(InventoryItem.objects.filter(barcode='5000112637922').count(), 2)

    def test_one_shop_cannot_reuse_a_barcode_on_a_live_product(self):
        InventoryItem.objects.create(shop=self.shop_a, name='Cola', barcode='111')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventoryItem.objects.create(shop=self.shop_a, name='Fake cola', barcode='111')

    def test_a_deleted_product_does_not_keep_reserving_its_barcode(self):
        InventoryItem.objects.create(shop=self.shop_a, name='Old', barcode='222', is_deleted=True)
        InventoryItem.objects.create(shop=self.shop_a, name='New', barcode='222')  # must not raise

    def test_products_without_a_barcode_never_collide(self):
        InventoryItem.objects.create(shop=self.shop_a, name='Loose 1', barcode=None)
        InventoryItem.objects.create(shop=self.shop_a, name='Loose 2', barcode=None)


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
