"""
_allocate_stock is what actually runs inside the locked transaction for
BOTH the REST create-sale path and the desktop sync path — these test it
directly rather than through the REST endpoint, because the endpoint's
own optimistic pre-check (SaleItemSerializer.validate, an unlocked read
of `item.quantity`) legitimately rejects an obviously-oversold request
before it ever reaches here. The real incident this guards against is
two requests racing each other, each passing that same optimistic check
because neither has committed yet when the other runs it — which an
in-process, single-threaded test can't reproduce (and SQLite ignores
select_for_update() regardless, same caveat as _allocate_stock's own
docstring — this only actually locks on Postgres).

What CAN be tested here, faithfully: once a SaleItem needs more than a
batch actually has left — whichever of the two racing requests that
turns out to be, after the other has already committed and taken what
was real — the shortfall gets recorded and broadcast instead of
silently vanishing into a slightly-wrong unit_cost with no trace.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop, RolePermission
from inventory.models import InventoryItem, StockBatch
from realtime.models import RealtimeEvent
from staff.models import Worker
from .models import Sale, SaleItem
from .views import _allocate_stock


def make_seller():
    user = User.objects.create_user(username='cashier1', password='x')
    org = Organization.objects.create(name='Test Shop', owner=None, business_type='general')
    shop = Shop.objects.create(name='Test Shop', organization=org)
    Worker.objects.create(user=user, full_name='Cashier One', role='seller', shop=shop)
    return user, shop


def make_worker(username, role, org=None, shop=None):
    """A worker of any role, reusing `org`/`shop` when given so several
    workers can share one branch — what the delete/edit-sale capability
    tests below need to compare roles against each other."""
    user = User.objects.create_user(username=username, password='x')
    if org is None:
        org = Organization.objects.create(name=f'{username} Shop', owner=None, business_type='general')
    if shop is None:
        shop = Shop.objects.create(name=org.name, organization=org)
    Worker.objects.create(user=user, full_name=username, role=role, shop=shop)
    return user, org, shop


class SaleDeletePermissionTests(TestCase):
    """The exact bug report this was written for: a seller could DELETE
    any sale, full stop -- SaleViewSet had no permission_classes at all
    restricting `destroy`. See SaleViewSet.get_permissions and
    core.capabilities for the fix."""

    def setUp(self):
        self.owner, self.org, self.shop = make_worker('owner1', 'owner')
        self.manager, _, _ = make_worker('manager1', 'branch_manager', org=self.org, shop=self.shop)
        self.seller, _, _ = make_worker('seller1', 'seller', org=self.org, shop=self.shop)
        self.reception, _, _ = make_worker('reception1', 'reception', org=self.org, shop=self.shop)

    def _sale(self):
        return Sale.objects.create(shop=self.shop, customer_name='Walk-in')

    def test_seller_cannot_delete_sale_by_default(self):
        sale = self._sale()
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Sale.objects.filter(id=sale.id, is_deleted=False).exists())

    def test_reception_cannot_delete_sale_by_default(self):
        sale = self._sale()
        client = APIClient()
        client.force_authenticate(user=self.reception)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 403)

    def test_owner_can_delete_sale(self):
        sale = self._sale()
        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 204)
        self.assertTrue(Sale.objects.get(id=sale.id).is_deleted)

    def test_branch_manager_can_delete_sale(self):
        # A branch manager gets the SAME "owner within my own branch"
        # authority an owner has (core.permissions.is_owner) -- this
        # never depends on the Control Center at all.
        sale = self._sale()
        client = APIClient()
        client.force_authenticate(user=self.manager)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 204)

    def test_control_center_can_grant_seller_delete_sale(self):
        RolePermission.objects.create(
            organization=self.org, role='seller', capability='delete_sale', allowed=True,
        )
        sale = self._sale()
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 204)

    def test_control_center_grant_is_scoped_to_one_organization(self):
        # Granting seller1's org the capability must not leak to a
        # DIFFERENT shop's seller.
        other_seller, other_org, other_shop = make_worker('seller2', 'seller')
        RolePermission.objects.create(
            organization=self.org, role='seller', capability='delete_sale', allowed=True,
        )
        sale = Sale.objects.create(shop=other_shop, customer_name='Walk-in')
        client = APIClient()
        client.force_authenticate(user=other_seller)
        res = client.delete(f'/api/sales/{sale.id}/')
        self.assertEqual(res.status_code, 403)


class SaleEditPermissionTests(TestCase):
    """Same gate, same reasoning, for rewriting what was rung up on an
    already-completed sale (replace-item) -- a seller quietly swapping a
    line after the fact is the same till-fraud shape as deleting the
    sale outright."""

    def setUp(self):
        self.owner, self.org, self.shop = make_worker('owner2', 'owner')
        self.seller, _, _ = make_worker('seller3', 'seller', org=self.org, shop=self.shop)

    def test_seller_cannot_replace_item_by_default(self):
        sale = Sale.objects.create(shop=self.shop, customer_name='Walk-in')
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post(f'/api/sales/{sale.id}/replace-item/', {}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_owner_reaches_serializer_validation_not_blocked_by_permission(self):
        sale = Sale.objects.create(shop=self.shop, customer_name='Walk-in')
        client = APIClient()
        client.force_authenticate(user=self.owner)
        # Empty body -- expected to fail *validation* (400), which proves
        # the permission check itself let the owner through.
        res = client.post(f'/api/sales/{sale.id}/replace-item/', {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_seller_granted_edit_sale_reaches_validation_too(self):
        RolePermission.objects.create(
            organization=self.org, role='seller', capability='edit_sale', allowed=True,
        )
        sale = Sale.objects.create(shop=self.shop, customer_name='Walk-in')
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post(f'/api/sales/{sale.id}/replace-item/', {}, format='json')
        self.assertEqual(res.status_code, 400)


class StockShortfallTests(TestCase):
    def setUp(self):
        _, self.shop = make_seller()
        self.item = InventoryItem.objects.create(
            shop=self.shop, name='Coke 50cl', category='other', unit='PIECE', sell_price=500,
        )

    def _make_sale_item(self, quantity):
        sale = Sale.objects.create(shop=self.shop, customer_name='Walk-in')
        return SaleItem.objects.create(
            sale=sale, shop=self.shop, item=self.item, item_name=self.item.name,
            quantity=quantity, unit_price=500,
        )

    def test_sufficient_stock_records_no_shortfall(self):
        StockBatch.objects.create(
            item=self.item, shop=self.shop, batch_number='B1',
            quantity_received=5, quantity_remaining=5, cost_price=300, selling_price=500,
        )
        sale_item = self._make_sale_item(2)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                _allocate_stock(sale_item, self.shop)

        sale_item.refresh_from_db()
        self.assertEqual(sale_item.stock_shortfall, 0)
        self.assertEqual(RealtimeEvent.objects.filter(event_type='stock.shortfall').count(), 0)

    def test_oversell_is_recorded_and_broadcast_not_blocked(self):
        """Simulates exactly the race's outcome: by the time this
        allocation runs, only 1 unit is actually left, but the SaleItem
        already committed to selling 3 — this is the SECOND of two racing
        sales, after the first already took what was real. The sale is
        never blocked or unwound over this; the customer already has the
        items in hand by the time this code runs."""
        StockBatch.objects.create(
            item=self.item, shop=self.shop, batch_number='B1',
            quantity_received=1, quantity_remaining=1, cost_price=300, selling_price=500,
        )
        sale_item = self._make_sale_item(3)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                _allocate_stock(sale_item, self.shop)

        sale_item.refresh_from_db()
        self.assertEqual(sale_item.quantity, 3)  # never altered — the recorded sale itself isn't touched
        self.assertEqual(sale_item.stock_shortfall, 2)  # asked for 3, only 1 was ever real

        batch = StockBatch.objects.get(item=self.item)
        self.assertEqual(batch.quantity_remaining, 0)  # floors at 0, never goes negative

        events = list(RealtimeEvent.objects.filter(event_type='stock.shortfall'))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload['shortfall'], 2)
        self.assertEqual(events[0].payload['requested'], 3)
        self.assertEqual(events[0].payload['item_name'], 'Coke 50cl')

    def test_service_line_with_no_linked_item_is_left_alone(self):
        """A custom/service line (no `item` FK — e.g. labour, a one-off
        charge) has no stock concept at all; _allocate_stock must no-op
        for it rather than error."""
        sale = Sale.objects.create(shop=self.shop, customer_name='Walk-in')
        sale_item = SaleItem.objects.create(
            sale=sale, shop=self.shop, item=None, item_name='Screen repair labour',
            quantity=1, unit_price=5000,
        )
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                _allocate_stock(sale_item, self.shop)  # must not raise
        sale_item.refresh_from_db()
        self.assertEqual(sale_item.stock_shortfall, 0)
