"""Stage 7: the audit log covers price changes, product deletion, discounts and till events, with
before -> after values; mobile money is its own payment method."""
import uuid

from django.test import TestCase

from core.models import RolePermission
from devices.models import Device
from inventory.models import InventoryItem, StockBatch
from sales.models import Sale, SalePayment
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op

from .models import AuditLog
from .tests import make_org


class Stage7Base(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.item = InventoryItem.objects.create(shop=self.shop, name='Rice', sell_price=1000)
        StockBatch.objects.create(item=self.item, shop=self.shop, batch_number='B', quantity_received=50,
                                  quantity_remaining=50, cost_price=700, selling_price=1000)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')

    def till_push(self, user, *ops):
        response, results = push_from(client_for(user), self.till, *ops)
        self.assertEqual(response.status_code, 200)
        return results

    def sale_doc(self, discount=0, method='cash'):
        sale_id = str(uuid.uuid4())
        return make_op('sale_doc', 'create', {
            'id': sale_id, 'customer_name': 'Walk-in', 'staff_name': 'Sola', 'status': 'completed', 'invoice_number': 3,
            'items': [{'id': str(uuid.uuid4()), 'product_id': str(self.item.id), 'item_name': 'Rice',
                       'quantity': 2, 'unit_price': 1000, 'discount': discount}],
            'payments': [{'id': str(uuid.uuid4()), 'method': method, 'amount': 2000 - discount}],
        }, entity_id=sale_id)


class ProductAuditTests(Stage7Base):
    def test_a_price_change_on_the_web_records_before_and_after(self):
        client_for(self.ceo).patch(f'/api/inventory/items/{self.item.id}/', {'sell_price': '1200.00'}, format='json')
        entry = AuditLog.objects.get(action='product.price_changed')
        self.assertEqual(entry.data['changes']['sell_price'], ['1000.00', '1200.00'])
        self.assertEqual(entry.user, self.ceo)

    def test_a_name_change_is_product_changed_and_no_change_records_nothing(self):
        client_for(self.ceo).patch(f'/api/inventory/items/{self.item.id}/', {'name': 'Rice 5kg'}, format='json')
        client_for(self.ceo).patch(f'/api/inventory/items/{self.item.id}/', {'name': 'Rice 5kg'}, format='json')
        self.assertEqual(AuditLog.objects.filter(action='product.changed').count(), 1)
        self.assertFalse(AuditLog.objects.filter(action='product.price_changed').exists())

    def test_deleting_a_product_is_audited(self):
        client_for(self.ceo).delete(f'/api/inventory/items/{self.item.id}/')
        entry = AuditLog.objects.get(action='product.deleted')
        self.assertIn('Rice', entry.summary)
        self.assertEqual(entry.data['quantity'], 50)

    def test_a_price_change_from_a_till_is_audited_too(self):
        op = make_op('product', 'update', {'name': 'Rice', 'sell_price': 900}, entity_id=self.item.id)
        self.assertEqual(self.till_push(self.ceo, op)[op['id']]['status'], 'applied')
        entry = AuditLog.objects.get(action='product.price_changed')
        self.assertEqual((entry.source, entry.device), ('till', self.till))
        self.assertEqual(entry.data['changes']['sell_price'][1], '900.00')


class DiscountAuditTests(Stage7Base):
    def test_a_discounted_till_sale_is_audited_once(self):
        op = self.sale_doc(discount=300)
        self.till_push(self.seller, op)
        self.till_push(self.seller, dict(op, id=str(uuid.uuid4())))  # the same sale re-sent
        entry = AuditLog.objects.get(action='sale.discount')
        self.assertEqual(entry.data['total_discount'], '300.00')
        self.assertEqual(entry.user, self.seller)

    def test_a_full_price_sale_is_not_audited(self):
        self.till_push(self.seller, self.sale_doc(discount=0))
        self.assertFalse(AuditLog.objects.filter(action='sale.discount').exists())


class TillAuditEventTests(Stage7Base):
    def event(self, action, **extra):
        return make_op('audit_event', 'create', dict({'action': action, 'actor': 'sola',
                                                       'created_at': '2026-10-19T07:00:00Z'}, **extra))

    def test_a_manual_sync_retry_on_the_till_reaches_the_audit_log_once(self):
        op = self.event('sync.retry', data={'retried': 3})
        self.till_push(self.seller, op)
        self.till_push(self.seller, dict(op, id=str(uuid.uuid4())))
        entry = AuditLog.objects.get(action='sync.retry')
        self.assertEqual((entry.source, entry.actor_name, entry.data), ('till', 'sola', {'retried': 3}))
        self.assertEqual(entry.at.isoformat(), '2026-10-19T07:00:00+00:00')

    def test_a_till_cannot_write_arbitrary_audit_actions(self):
        op = self.event('permissions.changed')
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'rejected')
        self.assertFalse(AuditLog.objects.filter(action='permissions.changed').exists())


class PermissionBeforeAfterTests(Stage7Base):
    def test_granting_records_before_and_after_and_a_no_op_records_nothing(self):
        put = lambda allowed: client_for(self.ceo).put('/api/control-center/', {'changes': [
            {'role': 'seller', 'capability': 'delete_sale', 'allowed': allowed}]}, format='json')
        put(False)  # already the default: nothing changes, nothing recorded
        self.assertFalse(AuditLog.objects.filter(action='permissions.changed').exists())
        put(True)
        entry = AuditLog.objects.get(action='permissions.changed')
        self.assertEqual(entry.data['changes'], {'allowed': ['False', 'True']})
        self.assertTrue(RolePermission.objects.get(role='seller', capability='delete_sale').allowed)


class MobileMoneyTests(Stage7Base):
    def test_a_till_sale_paid_by_mobile_money_keeps_that_method(self):
        op = self.sale_doc(method='mobile')
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'applied')
        self.assertEqual(SalePayment.objects.get(sale_id=op['entity_id']).method, 'mobile')

    def test_web_installment_can_be_paid_by_mobile_money(self):
        sale = Sale.objects.create(shop=self.shop, status='outstanding', worker=self.seller.worker)
        from sales.models import SaleItem
        SaleItem.objects.create(sale=sale, shop=self.shop, item_name='Rice', quantity=1, unit_price=1000)
        response = client_for(self.seller).post(f'/api/sales/{sale.id}/add-payment/', {'amount': 400, 'method': 'mobile'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(SalePayment.objects.get(sale=sale).method, 'mobile')

    def test_the_cloud_announces_protocol_5(self):
        body = client_for(self.seller).get('/api/sync/pull/', {'entity_types': 'customer'}).json()
        self.assertGreaterEqual(body['sync_protocol'], 5)
