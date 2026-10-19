"""Sellers see only their own sales; owners and roles granted view_all_sales see everyone's."""
import uuid

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import RolePermission
from devices.models import Device
from sales.models import Sale
from staff.models import Worker
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op, make_shop_user


def rows(response):
    body = response.json()
    return body.get('results', body)


class SellerSeesOwnSalesTests(TestCase):
    def setUp(self):
        self.sade, self.shop = make_shop_user('sade', 'Shop V')
        self.tunde = User.objects.create_user(username='tunde', password='x')
        Worker.objects.create(user=self.tunde, full_name='Tunde', role='seller', shop=self.shop)
        self.boss = User.objects.create_user(username='boss', password='x')
        Worker.objects.create(user=self.boss, full_name='Boss', role='owner', shop=self.shop)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')
        for user in (self.sade, self.tunde):
            sale_id = str(uuid.uuid4())
            op = make_op('sale_doc', 'create', {
                'id': sale_id, 'customer_name': 'Walk-in', 'status': 'completed', 'invoice_number': 1,
                'items': [{'id': str(uuid.uuid4()), 'item_name': 'Service', 'quantity': 1, 'unit_price': 100}],
                'payments': [{'id': str(uuid.uuid4()), 'method': 'cash', 'amount': 100}],
            }, entity_id=sale_id)
            push_from(client_for(user), self.till, op)
        # An old sale from before sales carried their worker: matched by name.
        Sale.objects.create(shop=self.shop, staff_name='Tunde', invoice_number=9)

    def test_a_seller_sees_only_their_own(self):
        self.assertEqual(len(rows(client_for(self.sade).get('/api/sales/'))), 1)
        self.assertEqual(len(rows(client_for(self.tunde).get('/api/sales/'))), 2)

    def test_owner_sees_everyone(self):
        self.assertEqual(len(rows(client_for(self.boss).get('/api/sales/'))), 3)

    def test_a_role_granted_view_all_sales_sees_everyone(self):
        RolePermission.objects.create(organization=self.shop.organization, role='seller', capability='view_all_sales', allowed=True)
        self.assertEqual(len(rows(client_for(self.sade).get('/api/sales/'))), 3)

    def test_a_seller_cannot_open_someone_elses_sale_by_id(self):
        other = Sale.objects.get(worker__user=self.tunde)
        self.assertEqual(client_for(self.sade).get(f'/api/sales/{other.id}/').status_code, 404)

    def test_tills_learn_who_sold_each_sale(self):
        body = client_for(self.boss).get('/api/sync/pull/', {'entity_types': 'sale'}, HTTP_X_DEVICE_ID=str(self.till.id)).json()
        sellers = sorted(filter(None, (op['payload']['seller_username'] for op in body['operations'])))
        self.assertEqual(sellers, ['sade', 'tunde'])

    def test_dashboard_takings_are_the_sellers_own(self):
        own = client_for(self.sade).get('/api/dashboard/stats/').json()['today_revenue']
        everyone = client_for(self.boss).get('/api/dashboard/stats/').json()['today_revenue']
        self.assertEqual(float(own), 100)
        self.assertEqual(float(everyone), 200)
