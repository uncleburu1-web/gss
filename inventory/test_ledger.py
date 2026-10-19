"""
Stage 4: the stock ledger.

Invariant checked throughout: for every live batch, the sum of its StockMovements equals its
quantity_remaining (inventory.ledger.drift() returns nothing).

    python manage.py test inventory
"""
import importlib
import uuid
from decimal import Decimal
from io import StringIO

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from devices.models import Device
from inventory import ledger
from inventory.models import InventoryItem, StockBatch, StockMovement
from repairs.models import RepairTicket
from sales.models import Sale
from staff.models import Worker
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op, make_shop_user

opening = importlib.import_module('inventory.migrations.0008_backfill_opening_movements')

D = Decimal


class LedgerBase(TestCase):
    def setUp(self):
        self.seller, self.shop = make_shop_user('sade', 'Shop L')
        self.owner = User.objects.create_user(username='boss', password='x')
        Worker.objects.create(user=self.owner, full_name='Boss', role='owner', shop=self.shop)
        self.item = InventoryItem.objects.create(shop=self.shop, name='Rice 5kg', sell_price=500)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')
        # Received through the API, so it gets its RECEIPT movement like any real stock.
        self.add_batch(10, 'B1')

    def add_batch(self, quantity, number):
        response = client_for(self.owner).post(
            f'/api/inventory/items/{self.item.id}/batches/',
            {'batch_number': number, 'quantity_received': quantity, 'cost_price': 300, 'selling_price': 500},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        return StockBatch.objects.get(item=self.item, batch_number=number)

    def stock(self):
        return ledger.on_hand(self.item)

    def assertNoDrift(self):
        self.assertEqual(ledger.drift(self.shop), [])

    def sale_doc(self, quantity):
        sale_id = str(uuid.uuid4())
        payload = {
            'id': sale_id, 'customer_name': 'Walk-in', 'staff_name': 'sade', 'payment_method': 'cash',
            'status': 'completed', 'invoice_number': 1,
            'items': [{'id': str(uuid.uuid4()), 'product_id': str(self.item.id), 'item_name': 'Rice',
                       'quantity': quantity, 'unit_price': 500, 'discount': 0}],
            'payments': [{'id': str(uuid.uuid4()), 'method': 'cash', 'amount': 500 * quantity}],
        }
        return make_op('sale_doc', 'create', payload, entity_id=sale_id)

    def till_push(self, user, *ops):
        response, results = push_from(client_for(user), self.till, *ops)
        self.assertEqual(response.status_code, 200)
        return results


class EveryStockChangeIsRecordedTests(LedgerBase):

    def test_receipt_is_recorded_with_who_did_it(self):
        movement = StockMovement.objects.get(kind=StockMovement.RECEIPT)
        self.assertEqual(movement.quantity, D('10'))
        self.assertEqual(movement.user, self.owner)
        self.assertEqual(movement.actor_name, 'Boss')
        self.assertNoDrift()

    def test_a_till_sale_and_its_void_are_both_recorded(self):
        op = self.sale_doc(3)
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'applied')
        sale_moves = StockMovement.objects.filter(kind=StockMovement.SALE)
        self.assertEqual(sum(m.quantity for m in sale_moves), D('-3'))
        self.assertEqual(sale_moves.first().source, 'till')
        self.assertEqual(self.stock(), 7)
        self.assertNoDrift()

        response = client_for(self.owner).delete(f'/api/sales/{op["entity_id"]}/')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(sum(m.quantity for m in StockMovement.objects.filter(kind=StockMovement.SALE_REVERSAL)), D('3'))
        self.assertEqual(self.stock(), 10)
        self.assertNoDrift()

    def test_a_sale_across_two_batches_records_one_movement_per_batch(self):
        self.add_batch(5, 'B2')
        op = self.sale_doc(12)
        self.till_push(self.seller, op)
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.SALE).count(), 2)
        self.assertNoDrift()

    def test_correcting_a_received_quantity_is_recorded(self):
        batch = StockBatch.objects.get(item=self.item, batch_number='B1')
        response = client_for(self.owner).patch(f'/api/inventory/batches/{batch.id}/', {'quantity_received': 12}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.stock(), 12)
        self.assertTrue(StockMovement.objects.filter(kind=StockMovement.RECEIPT, quantity=2).exists())
        self.assertNoDrift()

    def test_deleting_a_batch_records_what_left_with_it(self):
        batch = StockBatch.objects.get(item=self.item, batch_number='B1')
        response = client_for(self.owner).delete(f'/api/inventory/batches/{batch.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertTrue(StockMovement.objects.filter(kind=StockMovement.ADJUSTMENT, quantity=-10, reason='Batch deleted').exists())
        self.assertNoDrift()

    def test_repair_parts_are_recorded_and_a_shortfall_leaves_nothing_behind(self):
        ticket = RepairTicket.objects.create(shop=self.shop, customer_name='Ade', device='Phone')
        client = client_for(self.owner)
        too_many = client.post(f'/api/service/tickets/{ticket.id}/add-part/', {'item': str(self.item.id), 'quantity': 50}, format='json')
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(self.stock(), 10, 'a refused part must not leave a half-done deduction')
        self.assertNoDrift()

        ok = client.post(f'/api/service/tickets/{ticket.id}/add-part/', {'item': str(self.item.id), 'quantity': 2}, format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertTrue(StockMovement.objects.filter(kind=StockMovement.REPAIR_PART, quantity=-2).exists())
        removed = client.post(f'/api/service/tickets/{ticket.id}/remove-part/', {'part': ok.json()['id']}, format='json')
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.stock(), 10)
        self.assertNoDrift()

    def test_movements_are_append_only(self):
        movement = StockMovement.objects.first()
        movement.reason = 'rewritten'
        with self.assertRaises(ValueError):
            movement.save()
        with self.assertRaises(ValueError):
            movement.delete()


class OpeningBackfillTests(TestCase):

    def test_existing_stock_gets_one_opening_movement_and_a_rerun_adds_nothing(self):
        _user, shop = make_shop_user('olu', 'Old Shop')
        item = InventoryItem.objects.create(shop=shop, name='Soap', sell_price=100)
        live = StockBatch.objects.create(item=item, shop=shop, batch_number='X', quantity_received=9,
                                         quantity_remaining=4, cost_price=50, selling_price=100)
        empty = StockBatch.objects.create(item=item, shop=shop, batch_number='EMPTY', quantity_received=3,
                                          quantity_remaining=3, cost_price=50, selling_price=100)
        StockBatch.objects.filter(pk=empty.pk).update(quantity_remaining=0)  # sold out
        gone = StockBatch.objects.create(item=item, shop=shop, batch_number='GONE', quantity_received=5,
                                         quantity_remaining=5, cost_price=50, selling_price=100, is_deleted=True)
        self.assertNotEqual(ledger.drift(shop), [], 'before the backfill the ledger explains nothing')

        opening.forwards(django_apps, None)
        opening.forwards(django_apps, None)

        self.assertEqual(StockMovement.objects.filter(kind='opening').count(), 1)
        self.assertEqual(StockMovement.objects.get(kind='opening').batch_id, live.id)
        self.assertFalse(StockMovement.objects.filter(batch=gone).exists())
        self.assertEqual(ledger.drift(shop), [])
        self.assertEqual(StockBatch.objects.get(pk=live.pk).quantity_remaining, 4, 'stock itself untouched')


class DamageTests(LedgerBase):

    def damage(self, user, **body):
        return client_for(user).post(f'/api/inventory/items/{self.item.id}/damage/', body, format='json')

    def test_a_seller_can_record_damage_with_a_reason(self):
        response = self.damage(self.seller, quantity=2, reason='Bags torn by rats')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.stock(), 8)
        movement = StockMovement.objects.get(kind=StockMovement.DAMAGE)
        self.assertEqual((movement.quantity, movement.reason, movement.user), (D('-2'), 'Bags torn by rats', self.seller))
        self.assertIsNotNone(movement.created_at)
        self.assertNoDrift()

    def test_a_reason_is_mandatory(self):
        for reason in ('', '   '):
            response = self.damage(self.seller, quantity=1, reason=reason)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stock(), 10)
        self.assertFalse(StockMovement.objects.filter(kind=StockMovement.DAMAGE).exists())

    def test_cannot_write_off_more_than_is_in_stock_or_a_fraction(self):
        self.assertEqual(self.damage(self.seller, quantity=11, reason='flood').status_code, 400)
        self.assertEqual(self.damage(self.seller, quantity='1.5', reason='flood').status_code, 400)
        self.assertEqual(self.damage(self.seller, quantity=0, reason='flood').status_code, 400)
        self.assertEqual(self.stock(), 10)


class StockCountTests(LedgerBase):

    def count(self, user, **body):
        return client_for(user).post(f'/api/inventory/items/{self.item.id}/stock-count/', body, format='json')

    def test_a_seller_cannot_adjust_stock(self):
        response = self.count(self.seller, counted_quantity=3, reason='count')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stock(), 10)

    def test_an_owner_count_below_the_system_records_both_figures(self):
        response = self.count(self.owner, counted_quantity=7, reason='Monthly count')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.stock(), 7)
        movement = StockMovement.objects.get(kind=StockMovement.ADJUSTMENT)
        self.assertEqual((movement.quantity, movement.system_quantity, movement.counted_quantity), (D('-3'), D('10'), D('7')))
        self.assertEqual(movement.user, self.owner)
        self.assertNoDrift()

    def test_an_owner_count_above_the_system_adds_to_the_newest_batch(self):
        self.add_batch(4, 'B2')
        response = self.count(self.owner, counted_quantity=20, reason='Found a carton')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 20)
        self.assertEqual(StockBatch.objects.get(batch_number='B2').quantity_remaining, 10)
        self.assertNoDrift()

    def test_a_count_on_an_item_with_no_batches_creates_one(self):
        empty = InventoryItem.objects.create(shop=self.shop, name='New thing', sell_price=10)
        response = client_for(self.owner).post(f'/api/inventory/items/{empty.id}/stock-count/',
                                               {'counted_quantity': 6, 'reason': 'Opening count'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ledger.on_hand(empty), 6)
        self.assertNoDrift()

    def test_a_matching_count_changes_nothing_and_a_reason_is_required(self):
        self.assertEqual(self.count(self.owner, counted_quantity=10, reason='ok').status_code, 200)
        self.assertEqual(self.count(self.owner, counted_quantity=4, reason='').status_code, 400)
        self.assertFalse(StockMovement.objects.filter(kind=StockMovement.ADJUSTMENT).exists())


class MovementVisibilityTests(LedgerBase):

    def test_a_seller_sees_only_their_own_movements_and_an_owner_sees_all(self):
        client_for(self.seller).post(f'/api/inventory/items/{self.item.id}/damage/', {'quantity': 1, 'reason': 'broken'}, format='json')
        seller_rows = client_for(self.seller).get('/api/inventory/movements/').json()
        seller_rows = seller_rows.get('results', seller_rows)
        self.assertEqual([r['kind'] for r in seller_rows], ['damage'])

        owner_rows = client_for(self.owner).get('/api/inventory/movements/').json()
        owner_rows = owner_rows.get('results', owner_rows)
        self.assertEqual(sorted(r['kind'] for r in owner_rows), ['damage', 'receipt'])


class TillStockMovementSyncTests(LedgerBase):

    def damage_op(self, quantity, reason='Expired', doc_id=None):
        return make_op('stock_movement', 'create', {
            'kind': 'damage', 'product_id': str(self.item.id), 'quantity': quantity, 'reason': reason,
            'created_at': '2026-10-18T09:00:00Z', 'actor': 'sade',
        }, entity_id=doc_id)

    def test_a_till_damage_is_applied_once_and_remembers_the_till_document(self):
        op = self.damage_op(3)
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'applied')
        again = dict(op, id=str(uuid.uuid4()))  # same document re-sent under a new op id
        self.assertEqual(self.till_push(self.seller, again)[again['id']]['status'], 'applied')

        self.assertEqual(self.stock(), 7)
        movement = StockMovement.objects.get(kind=StockMovement.DAMAGE)
        self.assertEqual(str(movement.client_ref), op['entity_id'])
        self.assertEqual((movement.source, movement.actor_name, movement.user), ('till', 'sade', self.seller))
        self.assertNoDrift()

    def test_a_till_damage_without_a_reason_is_rejected(self):
        op = self.damage_op(1, reason='')
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'rejected')
        self.assertEqual(self.stock(), 10)

    def test_a_till_damage_bigger_than_cloud_stock_writes_off_what_is_left(self):
        op = self.damage_op(15)
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'applied')
        self.assertEqual(self.stock(), 0)
        self.assertNoDrift()

    def test_a_seller_till_cannot_push_a_stock_count(self):
        op = make_op('stock_movement', 'create', {'kind': 'adjustment', 'product_id': str(self.item.id),
                                                  'counted_quantity': 2, 'reason': 'count'})
        self.assertEqual(self.till_push(self.seller, op)[op['id']]['status'], 'rejected')
        self.assertEqual(self.stock(), 10)

    def test_an_owner_till_count_is_applied(self):
        op = make_op('stock_movement', 'create', {'kind': 'adjustment', 'product_id': str(self.item.id),
                                                  'counted_quantity': 2, 'reason': 'count'})
        self.assertEqual(self.till_push(self.owner, op)[op['id']]['status'], 'applied')
        self.assertEqual(self.stock(), 2)
        self.assertNoDrift()

    def test_tills_pull_damage_but_not_sales_or_receipts(self):
        self.till_push(self.seller, self.sale_doc(1), self.damage_op(1))
        body = client_for(self.seller).get('/api/sync/pull/', {'entity_types': 'stock_movement'},
                                           HTTP_X_DEVICE_ID=str(self.till.id)).json()
        kinds = [op['payload']['kind'] for op in body['operations']]
        self.assertEqual(kinds, ['damage'])
        self.assertIsNotNone(body['operations'][0]['payload']['client_ref'])
        self.assertGreaterEqual(body['sync_protocol'], 4)


class DriftCommandTests(LedgerBase):

    def test_clean_ledger_exits_quietly_and_drift_exits_1(self):
        out = StringIO()
        call_command('check_stock_drift', stdout=out)
        self.assertIn('matches', out.getvalue())

        StockBatch.objects.filter(item=self.item).update(quantity_remaining=99)  # a change that bypassed the ledger
        with self.assertRaises(SystemExit) as raised:
            call_command('check_stock_drift', stdout=StringIO())
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(ledger.drift(self.shop)[0]['difference'], '89.000')


class TillBatchReceiptTests(LedgerBase):

    def test_stock_received_on_a_till_is_recorded(self):
        batch_id = str(uuid.uuid4())
        op = make_op('stock_batch', 'create', {'product_id': str(self.item.id), 'batch_number': 'T1',
                                               'quantity_received': 6, 'quantity_remaining': 6, 'cost_price': 300},
                     entity_id=batch_id)
        self.assertEqual(self.till_push(self.owner, op)[op['id']]['status'], 'applied')
        movement = StockMovement.objects.get(batch_id=batch_id)
        self.assertEqual((movement.kind, movement.quantity, movement.source), ('receipt', D('6'), 'till'))
        self.assertNoDrift()
