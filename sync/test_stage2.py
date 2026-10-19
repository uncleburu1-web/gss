"""
Stage 2 regression tests (architecture analysis G1 and G2).

G1  A whole sale (header, lines, payments) arrives as ONE `sale_doc` operation and is applied in
    one transaction: the cloud has all of it or none of it.
G2  Payments are append-only rows. Sale.amount_paid is derived from them, so payments taken on
    different devices both count instead of overwriting each other.

Invariant checked throughout: sale.amount_paid == sum of its live payments.

    python manage.py test sync
"""
import importlib
import uuid
from decimal import Decimal

from django.apps import apps as django_apps
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from devices.models import Device
from inventory.models import InventoryItem, StockBatch
from sales.models import Sale, SaleItem, SalePayment
from sales.payments import legacy_payment_id

from .models import SyncOperation
from .test_stage1 import push_from
from .tests import client_for, make_op, make_shop_user, push

backfill = importlib.import_module('sales.migrations.0007_backfill_sale_payments')

D = Decimal


def paid_sum(sale):
    return SalePayment.objects.filter(sale=sale, is_deleted=False).aggregate(s=Sum('amount'))['s'] or D('0')


class Stage2Base(TestCase):
    def setUp(self):
        self.user, self.shop = make_shop_user('tina', 'Shop T')
        self.client_ = client_for(self.user)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')
        self.item = InventoryItem.objects.create(shop=self.shop, name='Malt', sell_price=500)
        StockBatch.objects.create(item=self.item, shop=self.shop, batch_number='B1', quantity_received=20,
                                  quantity_remaining=20, cost_price=300, selling_price=500)

    def stock(self):
        return sum(b.quantity_remaining for b in StockBatch.objects.filter(item=self.item))

    def assertInvariant(self, sale):
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, paid_sum(sale))

    def sale_doc(self, quantity=2, price=500, payments=None, status='completed', product_id=None, **extra):
        sale_id = str(uuid.uuid4())
        total = quantity * price
        if payments is None:
            payments = [{'method': 'cash', 'amount': total, 'tendered': total, 'change_given': 0}]
        payload = {
            'id': sale_id, 'customer_name': 'Walk-in', 'staff_name': 'tina', 'payment_method': 'cash',
            'status': status, 'invoice_number': 7, 'receipt_no': 'A1B2C3-000007',
            'date': timezone.now().isoformat(),
            'items': [{'id': str(uuid.uuid4()), 'product_id': product_id or str(self.item.id), 'item_name': 'Malt',
                       'category': 'beverage', 'quantity': quantity, 'unit_price': price, 'discount': 0}],
            'payments': [dict({'id': str(uuid.uuid4()), 'paid_at': timezone.now().isoformat()}, **p) for p in payments],
        }
        payload.update(extra)
        return make_op('sale_doc', 'create', payload, entity_id=sale_id)

    def till_push(self, *ops):
        response, results = push_from(self.client_, self.till, *ops)
        self.assertEqual(response.status_code, 200)
        return results


class SaleDocTests(Stage2Base):
    """G1."""

    def test_a_whole_sale_lands_in_one_operation(self):
        op = self.sale_doc(quantity=3)
        results = self.till_push(op)

        self.assertEqual(results[op['id']]['status'], 'applied')
        sale = Sale.objects.get(id=op['entity_id'])
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.total, D('1500'))
        self.assertEqual(sale.amount_paid, D('1500'))
        self.assertEqual(sale.status, 'completed')
        self.assertEqual(sale.device, self.till)
        self.assertIsNotNone(sale.received_at)
        self.assertEqual(sale.receipt_no, 'A1B2C3-000007')
        self.assertEqual(self.stock(), 17)
        self.assertEqual(SalePayment.objects.get(sale=sale).source, 'till')
        self.assertInvariant(sale)

    def test_a_missing_product_holds_back_the_WHOLE_sale_and_it_applies_later(self):
        missing_product = str(uuid.uuid4())
        op = self.sale_doc(product_id=missing_product)
        results = self.till_push(op)

        self.assertNotIn(op['id'], results, 'deferred, not rejected')
        self.assertFalse(Sale.objects.filter(id=op['entity_id']).exists(), 'no header without its lines')
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(SyncOperation.objects.filter(id=op['id']).exists())

        InventoryItem.objects.create(id=missing_product, shop=self.shop, name='Late product', sell_price=500)
        results = self.till_push(op)
        self.assertEqual(results[op['id']]['status'], 'applied')
        self.assertEqual(Sale.objects.get(id=op['entity_id']).items.count(), 1)

    def test_a_broken_line_rejects_the_whole_sale_and_leaves_nothing_behind(self):
        op = self.sale_doc()
        op['payload']['items'].append({'product_id': str(self.item.id), 'item_name': 'no id', 'quantity': 1, 'unit_price': 5})
        results = self.till_push(op)

        self.assertEqual(results[op['id']]['status'], 'rejected')
        self.assertFalse(Sale.objects.filter(id=op['entity_id']).exists())
        self.assertEqual(self.stock(), 20, 'no stock taken for a rejected sale')

    def test_a_sale_with_no_lines_is_rejected(self):
        op = self.sale_doc()
        op['payload']['items'] = []
        self.assertEqual(self.till_push(op)[op['id']]['status'], 'rejected')

    def test_a_replayed_sale_doc_is_acknowledged_and_takes_stock_once(self):
        op = self.sale_doc(quantity=4)
        self.till_push(op)
        results = self.till_push(op)

        self.assertEqual(results[op['id']]['status'], 'already_applied')
        self.assertEqual(self.stock(), 16)
        self.assertEqual(SalePayment.objects.count(), 1)

    def test_the_same_sale_resent_under_a_NEW_op_id_still_cannot_double(self):
        op = self.sale_doc(quantity=4)
        self.till_push(op)
        again = dict(op, id=str(uuid.uuid4()))
        self.assertEqual(self.till_push(again)[again['id']]['status'], 'applied')
        self.assertEqual(SaleItem.objects.filter(sale_id=op['entity_id']).count(), 1)
        self.assertEqual(self.stock(), 16)
        self.assertEqual(SalePayment.objects.count(), 1)

    def test_a_split_payment_keeps_every_method(self):
        op = self.sale_doc(quantity=2, payments=[
            {'method': 'cash', 'amount': 300, 'tendered': 500, 'change_given': 200},
            {'method': 'transfer', 'amount': 500, 'reference': 'GTB-1234'},
            {'method': 'pos', 'amount': 200},
        ])
        self.till_push(op)
        sale = Sale.objects.get(id=op['entity_id'])

        by_method = {p.method: p for p in sale.payments.all()}
        self.assertEqual(set(by_method), {'cash', 'transfer', 'pos'})
        self.assertEqual(by_method['cash'].change_given, D('200'))
        self.assertEqual(by_method['transfer'].reference, 'GTB-1234')
        self.assertEqual(sale.amount_paid, D('1000'))
        self.assertInvariant(sale)

    def test_web_and_other_tills_see_the_payments_when_they_pull(self):
        op = self.sale_doc()
        self.till_push(op)
        body = self.client_.get('/api/sync/pull/', {'entity_types': 'sale'}).json()

        self.assertGreaterEqual(body['sync_protocol'], 2)
        [pulled] = body['operations']
        self.assertEqual(len(pulled['payload']['payments']), 1)
        self.assertEqual(pulled['payload']['payments'][0]['source'], 'till')


class InstallmentTests(Stage2Base):
    """G2."""

    def outstanding_sale(self, deposit=200):
        op = self.sale_doc(quantity=2, status='outstanding', payments=[{'method': 'cash', 'amount': deposit}])
        self.till_push(op)
        return Sale.objects.get(id=op['entity_id'])

    def till_payment(self, sale, amount, **extra):
        return make_op('sale_payment', 'create', dict({
            'sale_id': str(sale.id), 'method': 'transfer', 'amount': amount, 'paid_at': timezone.now().isoformat(),
        }, **extra))

    def test_a_web_payment_and_an_offline_till_payment_BOTH_count(self):
        sale = self.outstanding_sale(deposit=200)              # total 1000, 200 paid
        r = self.client_.post(f'/api/sales/{sale.id}/add-payment/', {'amount': '300', 'method': 'pos'}, format='json')
        self.assertEqual(r.status_code, 200)
        op = self.till_payment(sale, 500)                      # taken offline before the till knew about the 300
        self.assertEqual(self.till_push(op)[op['id']]['status'], 'applied')

        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, D('1000'))
        self.assertEqual(sale.status, 'completed')
        self.assertEqual(sale.payments.count(), 3)
        self.assertInvariant(sale)

    def test_a_replayed_till_payment_counts_once(self):
        sale = self.outstanding_sale()
        op = self.till_payment(sale, 100)
        self.till_push(op)
        self.assertEqual(self.till_push(op)[op['id']]['status'], 'already_applied')
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, D('300'))

    def test_a_payment_whose_sale_has_not_arrived_waits(self):
        op = make_op('sale_payment', 'create', {'sale_id': str(uuid.uuid4()), 'amount': 100})
        self.assertNotIn(op['id'], self.till_push(op))

    def test_a_payment_on_a_deleted_sale_is_rejected_visibly(self):
        sale = self.outstanding_sale()
        Sale.objects.filter(pk=sale.pk).update(is_deleted=True)
        op = self.till_payment(sale, 100)
        self.assertEqual(self.till_push(op)[op['id']]['status'], 'rejected')

    def test_an_OLDER_till_can_no_longer_erase_a_web_payment(self):
        # Old protocol: header + line, then an installment as an absolute amount_paid.
        sale_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
        push(self.client_,
             make_op('sale', 'create', {'customer_name': 'X', 'status': 'outstanding', 'amount_paid': 200, 'invoice_number': 1}, entity_id=sale_id),
             make_op('sale_item', 'create', {'sale_id': sale_id, 'product_id': str(self.item.id), 'item_name': 'Malt',
                                             'quantity': 2, 'unit_price': 500}, entity_id=item_id))
        sale = Sale.objects.get(id=sale_id)
        self.assertEqual(sale.amount_paid, D('200'))
        self.client_.post(f'/api/sales/{sale.id}/add-payment/', {'amount': '300'}, format='json')

        # The old till still believes 200 was paid and records +200 on its own copy: it sends 400.
        push(self.client_, make_op('sale', 'update', {'customer_name': 'X', 'status': 'outstanding', 'amount_paid': 400}, entity_id=sale_id))
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, D('500'), 'before stage 2 this became 400: the web payment was lost')
        self.assertInvariant(sale)


class RestPathsKeepTheInvariantTests(Stage2Base):
    def setUp(self):
        super().setUp()
        # Replacing an item and deleting a sale are owner/manager capabilities.
        self.user.worker.role = 'owner'
        self.user.worker.save(update_fields=['role'])

    def create(self, **data):
        body = dict({'customer_name': 'Walk-in', 'payment_method': 'cash', 'status': 'completed',
                     'items': [{'item': str(self.item.id), 'item_name': 'Malt', 'quantity': 2, 'unit_price': '500'}]}, **data)
        r = self.client_.post('/api/sales/', body, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return Sale.objects.get(id=r.json()['id'])

    def test_a_completed_web_sale_has_one_payment_for_its_total(self):
        sale = self.create()
        self.assertEqual(sale.amount_paid, D('1000'))
        self.assertEqual(sale.payments.get().source, 'web')
        self.assertInvariant(sale)

    def test_an_installment_web_sale_records_its_deposit_and_later_payments(self):
        sale = self.create(status='outstanding', amount_paid='250')
        self.assertEqual(sale.amount_paid, D('250'))
        r = self.client_.post(f'/api/sales/{sale.id}/add-payment/', {'amount': '9999'}, format='json')
        self.assertEqual(r.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, D('1000'), 'only the balance is applied, never more')
        self.assertEqual(sale.status, 'completed')
        self.assertInvariant(sale)

    def test_swapping_an_item_records_the_difference_as_a_payment(self):
        sale = self.create()
        line = sale.items.get()
        r = self.client_.post(f'/api/sales/{sale.id}/replace-item/', {
            'sale_item': str(line.id), 'item': str(self.item.id), 'item_name': 'Malt', 'quantity': 1, 'unit_price': '500',
        }, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, D('500'))
        self.assertEqual(sorted(p.amount for p in sale.payments.all()), [D('-500'), D('1000')])
        self.assertInvariant(sale)

    def test_deleting_a_sale_retires_its_payments(self):
        sale = self.create()
        self.assertEqual(self.client_.delete(f'/api/sales/{sale.id}/').status_code, 204)
        self.assertFalse(sale.payments.filter(is_deleted=False).exists())


class BackfillMigrationTests(Stage2Base):
    def test_every_existing_sale_gets_exactly_one_legacy_payment_and_reruns_are_harmless(self):
        old = Sale.objects.create(shop=self.shop, customer_name='Old', payment_method='transfer', status='completed')
        Sale.objects.filter(pk=old.pk).update(amount_paid=D('750'))
        unpaid = Sale.objects.create(shop=self.shop, customer_name='Owes', status='outstanding')

        backfill.forwards(django_apps, None)
        backfill.forwards(django_apps, None)

        payment = SalePayment.objects.get(sale=old)
        self.assertEqual(payment.id, legacy_payment_id(old.id))
        self.assertEqual((payment.amount, payment.method, payment.source), (D('750'), 'transfer', 'legacy'))
        self.assertFalse(SalePayment.objects.filter(sale=unpaid).exists())
        old.refresh_from_db()
        self.assertEqual(old.amount_paid, D('750'), 'amount_paid itself is untouched')


class RepeatedCreateTests(Stage2Base):
    """Older tills re-sent their whole history as 'create' operations at every startup. A
    repeated create must never overwrite newer cloud data."""

    def test_a_resent_product_create_does_not_undo_an_owner_price_change(self):
        product_id = str(uuid.uuid4())
        self.user.worker.role = 'owner'
        self.user.worker.save(update_fields=['role'])
        push(self.client_, make_op('product', 'create', {'name': 'Gin', 'sell_price': 1000}, entity_id=product_id))
        InventoryItem.objects.filter(id=product_id).update(sell_price=1500)        # owner edits on the web
        _, results = push(self.client_, make_op('product', 'create', {'name': 'Gin', 'sell_price': 1000}, entity_id=product_id))

        self.assertEqual(list(results.values())[0]['status'], 'already_applied')
        self.assertEqual(InventoryItem.objects.get(id=product_id).sell_price, D('1500'))

    def test_a_resent_batch_create_does_not_overwrite_the_clouds_remaining_stock(self):
        self.user.worker.role = 'owner'
        self.user.worker.save(update_fields=['role'])
        batch = StockBatch.objects.get(item=self.item)
        StockBatch.objects.filter(pk=batch.pk).update(quantity_remaining=12)         # other tills sold 8
        push(self.client_, make_op('stock_batch', 'create', {
            'product_id': str(self.item.id), 'quantity_received': 20, 'quantity_remaining': 19, 'cost_price': 300,
        }, entity_id=str(batch.id)))
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, 12)

    def test_a_resent_legacy_sale_header_does_not_reset_a_completed_installment(self):
        sale_id = str(uuid.uuid4())
        header = {'customer_name': 'X', 'status': 'outstanding', 'amount_paid': 200, 'invoice_number': 1}
        push(self.client_, make_op('sale', 'create', header, entity_id=sale_id),
             make_op('sale_item', 'create', {'sale_id': sale_id, 'product_id': str(self.item.id), 'item_name': 'Malt',
                                             'quantity': 2, 'unit_price': 500}))
        self.client_.post(f'/api/sales/{sale_id}/add-payment/', {'amount': '800'}, format='json')
        push(self.client_, make_op('sale', 'create', header, entity_id=sale_id))

        sale = Sale.objects.get(id=sale_id)
        self.assertEqual((sale.status, sale.amount_paid), ('completed', D('1000')))

    def test_a_resent_sale_doc_keeps_the_clouds_header(self):
        op = self.sale_doc(status='outstanding', payments=[{'method': 'cash', 'amount': 100}])
        self.till_push(op)
        Sale.objects.filter(id=op['entity_id']).update(customer_name='Corrected on the web')
        self.till_push(dict(op, id=str(uuid.uuid4())))
        self.assertEqual(Sale.objects.get(id=op['entity_id']).customer_name, 'Corrected on the web')

    def test_a_sellers_till_resending_an_existing_product_is_not_a_failure(self):
        # self.user is a seller here; the product already exists in the cloud.
        _, results = push(self.client_, make_op('product', 'create', {'name': 'Malt', 'sell_price': 1}, entity_id=str(self.item.id)))
        self.assertEqual(list(results.values())[0]['status'], 'already_applied')
        self.assertEqual(InventoryItem.objects.get(id=self.item.id).sell_price, D('500'))

    def test_a_seller_still_cannot_create_a_NEW_product(self):
        _, results = push(self.client_, make_op('product', 'create', {'name': 'New', 'sell_price': 1}))
        self.assertEqual(list(results.values())[0]['status'], 'rejected')
