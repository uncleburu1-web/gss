"""Stage 8: customer returns and refunds."""
import uuid
from decimal import Decimal

from django.test import TestCase

from audit.models import AuditLog
from audit.tests import make_org
from core.models import RolePermission
from devices.models import Device
from inventory import ledger
from inventory.models import InventoryItem, StockBatch, StockMovement
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op

from .models import Sale, SaleReturn

D = Decimal


class ReturnBase(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.item = InventoryItem.objects.create(shop=self.shop, name='Juice', sell_price=1000)
        self.batch = StockBatch.objects.create(item=self.item, shop=self.shop, batch_number='B', quantity_received=20,
                                               quantity_remaining=20, cost_price=600, selling_price=1000)
        StockMovement.objects.create(shop=self.shop, item=self.item, batch=self.batch, kind='opening', quantity=20)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')
        sale_id = str(uuid.uuid4())
        self.line_id = str(uuid.uuid4())
        op = make_op('sale_doc', 'create', {
            'id': sale_id, 'customer_name': 'Walk-in', 'status': 'completed', 'invoice_number': 4,
            'items': [{'id': self.line_id, 'product_id': str(self.item.id), 'item_name': 'Juice',
                       'quantity': 5, 'unit_price': 1000, 'discount': 500}],     # 5 x 1000 - 500 = 4500
            'payments': [{'id': str(uuid.uuid4()), 'method': 'cash', 'amount': 4500}],
        }, entity_id=sale_id)
        push_from(client_for(self.seller), self.till, op)
        self.sale = Sale.objects.get(id=sale_id)

    def stock(self):
        return ledger.on_hand(self.item)

    def take_return(self, user, **body):
        body.setdefault('reason', 'Wrong flavour')
        body.setdefault('lines', [{'sale_item': self.line_id, 'quantity': 2}])
        return client_for(user).post(f'/api/sales/{self.sale.id}/return/', body, format='json')


class WebReturnTests(ReturnBase):
    def test_owner_takes_a_return_stock_goes_back_and_refund_is_the_discounted_value(self):
        response = self.take_return(self.ceo)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['refund_amount'], '1800.00')   # 2/5 of 4500
        self.assertEqual(self.stock(), 17)                                 # 20 - 5 + 2
        self.assertEqual(ledger.drift(self.shop), [])
        self.assertEqual(response.json()['sale']['returned_quantities'], {self.line_id: 2})
        self.assertTrue(AuditLog.objects.filter(action='sale.returned', reason='Wrong flavour').exists())

    def test_damaged_items_are_written_off_not_restocked(self):
        self.take_return(self.ceo, lines=[{'sale_item': self.line_id, 'quantity': 1, 'restock': False}])
        self.assertEqual(self.stock(), 15)
        self.assertEqual(ledger.drift(self.shop), [])

    def test_cannot_return_more_than_sold_across_several_returns(self):
        self.assertEqual(self.take_return(self.ceo, lines=[{'sale_item': self.line_id, 'quantity': 4}]).status_code, 201)
        response = self.take_return(self.ceo, lines=[{'sale_item': self.line_id, 'quantity': 2}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Only 1', response.json()['detail'])

    def test_reason_is_required_and_refund_cannot_exceed_value(self):
        self.assertEqual(self.take_return(self.ceo, reason='').status_code, 400)
        self.assertEqual(self.take_return(self.ceo, refund_amount='1900').status_code, 400)
        self.assertEqual(self.take_return(self.ceo, refund_amount='1000', refund_method='mobile').status_code, 201)
        self.assertEqual(SaleReturn.objects.get().refund_amount, D('1000.00'))

    def test_sellers_need_the_capability(self):
        self.assertEqual(self.take_return(self.seller).status_code, 403)
        RolePermission.objects.create(organization=self.org, role='seller', capability='process_return', allowed=True)
        self.assertEqual(self.take_return(self.seller).status_code, 201)

    def test_a_returned_sale_can_no_longer_be_deleted_or_edited(self):
        self.take_return(self.ceo)
        self.assertEqual(client_for(self.ceo).delete(f'/api/sales/{self.sale.id}/').status_code, 400)
        self.assertEqual(self.stock(), 17, 'nothing restored twice')

    def test_voiding_after_a_damaged_return_cannot_bring_units_back(self):
        # The allocation is reduced on return, so even a forced void restores only unreturned units.
        from .views import _restore_stock
        self.take_return(self.ceo, lines=[{'sale_item': self.line_id, 'quantity': 2, 'restock': False}])
        _restore_stock(self.sale.items.get())
        self.assertEqual(self.stock(), 18)   # 15 left + only the 3 not returned

    def test_reports_net_out_returns_in_the_period(self):
        before = client_for(self.ceo).get('/api/reports/sales-summary/', {'period': 'today'}).json()
        self.take_return(self.ceo)
        after = client_for(self.ceo).get('/api/reports/sales-summary/', {'period': 'today'}).json()
        self.assertEqual(D(str(before['product_sales'])) - D(str(after['product_sales'])), D('1800'))
        self.assertEqual(after['items_sold'], 3)
        self.assertEqual(D(str(after['refunds_paid'])), D('1800'))
        stats = client_for(self.ceo).get('/api/dashboard/stats/').json()
        self.assertEqual(D(str(stats['today_revenue'])), D('2700'))


class TillReturnTests(ReturnBase):
    def op(self, **extra):
        payload = {'sale_id': str(self.sale.id), 'lines': [{'sale_item_id': self.line_id, 'quantity': 1, 'restock': True}],
                   'reason': 'Leaking', 'refund_method': 'cash', 'refund_amount': 900, 'created_at': '2026-10-19T10:00:00Z',
                   'actor': 'boss'}
        payload.update(extra)
        return make_op('sale_return', 'create', payload)

    def test_a_till_return_is_applied_once_with_the_tills_id(self):
        op = self.op()
        _, results = push_from(client_for(self.ceo), self.till, op)
        self.assertEqual(results[op['id']]['status'], 'applied')
        push_from(client_for(self.ceo), self.till, dict(op, id=str(uuid.uuid4())))
        ret = SaleReturn.objects.get()
        self.assertEqual(str(ret.id), op['entity_id'])
        self.assertEqual((ret.source, ret.actor_name, ret.refund_amount), ('till', 'boss', D('900.00')))
        self.assertEqual(self.stock(), 16)
        movement = StockMovement.objects.get(kind='return')
        self.assertEqual(str(movement.client_ref), op['entity_id'], 'the till recognises its own return on pull')

    def test_a_seller_till_without_the_capability_is_refused(self):
        op = self.op()
        _, results = push_from(client_for(self.seller), self.till, op)
        self.assertEqual(results[op['id']]['status'], 'rejected')
        self.assertEqual(self.stock(), 15)

    def test_a_return_for_a_sale_not_yet_uploaded_waits(self):
        op = self.op(sale_id=str(uuid.uuid4()))
        _, results = push_from(client_for(self.ceo), self.till, op)
        self.assertNotIn(op['id'], results)

    def test_tills_pull_web_returns_as_stock_movements(self):
        self.take_return(self.ceo)
        body = client_for(self.ceo).get('/api/sync/pull/', {'entity_types': 'stock_movement'}, HTTP_X_DEVICE_ID=str(self.till.id)).json()
        self.assertEqual([o['payload']['kind'] for o in body['operations']], ['return'])


class ReturnIdempotencyTests(ReturnBase):
    def test_a_retried_return_with_the_same_key_refunds_once(self):
        client = client_for(self.ceo)
        body = {'reason': 'Leak', 'lines': [{'sale_item': self.line_id, 'quantity': 1}]}
        first = client.post(f'/api/sales/{self.sale.id}/return/', body, format='json', HTTP_IDEMPOTENCY_KEY='ret-key-123')
        second = client.post(f'/api/sales/{self.sale.id}/return/', body, format='json', HTTP_IDEMPOTENCY_KEY='ret-key-123')
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()['return_id'], second.json()['return_id'])
        self.assertEqual(SaleReturn.objects.count(), 1)
        self.assertEqual(self.stock(), 16)
