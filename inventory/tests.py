from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Organization, Shop
from .models import InventoryItem


class BarcodeUniquenessTests(TestCase):
    """Barcodes must be unique per branch only -- never across the whole
    database. A second branch (or a completely different shop) has to be
    able to use a code another branch already holds."""

    def setUp(self):
        self.org = Organization.objects.create(name='Acme Stores')
        self.branch_a = Shop.objects.create(organization=self.org, name='Wuse', branch_code='WUSE001')
        self.branch_b = Shop.objects.create(organization=self.org, name='Garki', branch_code='GARKI001')
        other_org = Organization.objects.create(name='Other Business')
        self.other_shop = Shop.objects.create(organization=other_org, name='Other Shop')

    def _item(self, shop, barcode, **extra):
        return InventoryItem.objects.create(shop=shop, name='Item', barcode=barcode, **extra)

    def test_same_barcode_allowed_in_another_branch_of_same_business(self):
        self._item(self.branch_a, '6001234567890')
        self._item(self.branch_b, '6001234567890')  # must not raise
        self.assertEqual(InventoryItem.objects.filter(barcode='6001234567890').count(), 2)

    def test_same_barcode_allowed_in_a_different_business(self):
        self._item(self.branch_a, '6001234567890')
        self._item(self.other_shop, '6001234567890')  # must not raise

    def test_same_barcode_rejected_inside_one_branch(self):
        self._item(self.branch_a, '6001234567890')
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._item(self.branch_a, '6001234567890')

    def test_soft_deleted_product_frees_its_barcode(self):
        old = self._item(self.branch_a, '6001234567890')
        old.is_deleted = True
        old.save()
        self._item(self.branch_a, '6001234567890')  # must not raise

    def test_many_products_without_a_barcode_never_collide(self):
        self._item(self.branch_a, None)
        self._item(self.branch_a, None)
        self._item(self.branch_a, '')
        self._item(self.branch_a, '')
