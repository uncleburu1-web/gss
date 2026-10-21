"""Stage 11: products sold by weight (kg, to the gram). Every other product stays whole units."""
import uuid
from decimal import Decimal

from django.test import TestCase

from audit.tests import make_org
from devices.models import Device
from inventory import ledger
from inventory.models import InventoryItem, StockBatch
from sales.models import Sale, SaleItem
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op

D = Decimal


class WeightBase(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.rice = InventoryItem.objects.create(shop=self.shop, name='Rice (loose)', sell_price=1500, sold_by_weight=True, unit='KG')
        self.tin = InventoryItem.objects.create(shop=self.shop, name='Milk tin', sell_price=900)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')
        owner = client_for(self.ceo)
        for item, qty in ((self.rice, '25.500'), (self.tin, '10')):
            r = owner.post(f'/api/inventory/items/{item.id}/batches/', {'batch_number': 'B', 'quantity_received': qty,
                                                                        'cost_price': 1000, 'selling_price': item.sell_price}, format='json')
            self.assertEqual(r.status_code, 201, r.content)

    def sell(self, item, quantity):
        return client_for(self.seller).post('/api/sales/', {'customer_name': 'Walk-in', 'payment_method': 'cash', 'status': 'completed',
            'items': [{'item': str(item.id), 'item_name': item.name, 'quantity': quantity, 'unit_price': str(item.sell_price)}]}, format='json')


class WeighedSaleTests(WeightBase):
    def test_kg_are_sold_priced_per_kg_and_come_back_as_numbers(self):
        response = self.sell(self.rice, 1.25)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['items'][0]['quantity'], 1.25)            # a number, not "1.250"
        self.assertEqual(D(str(body['total'])), D('1875.00'))            # 1.25 kg x 1500
        self.assertEqual(ledger.on_hand(self.rice), D('24.250'))
        self.assertEqual(ledger.drift(self.shop), [])

    def test_more_than_three_decimals_is_refused(self):
        self.assertEqual(self.sell(self.rice, 0.1234).status_code, 400)

    def test_whole_unit_products_still_refuse_fractions(self):
        response = self.sell(self.tin, 1.5)
        self.assertEqual(response.status_code, 400)
        self.assertIn('whole number', str(response.json()))
        self.assertEqual(self.sell(self.tin, 2).status_code, 201)

    def test_existing_whole_quantities_still_read_as_whole_numbers(self):
        self.sell(self.tin, 3)
        line = client_for(self.ceo).get('/api/sales/').json()
        line = (line.get('results', line))[0]['items'][0]
        self.assertEqual(line['quantity'], 3)

    def test_a_till_sells_by_weight_and_the_flag_reaches_tills(self):
        sale_id = str(uuid.uuid4())
        op = make_op('sale_doc', 'create', {
            'id': sale_id, 'status': 'completed', 'invoice_number': 1,
            'items': [{'id': str(uuid.uuid4()), 'product_id': str(self.rice.id), 'item_name': 'Rice', 'quantity': 0.7, 'unit_price': 1500}],
            'payments': [{'id': str(uuid.uuid4()), 'method': 'cash', 'amount': 1050}],
        }, entity_id=sale_id)
        _, results = push_from(client_for(self.seller), self.till, op)
        self.assertEqual(results[op['id']]['status'], 'applied')
        self.assertEqual(SaleItem.objects.get(sale_id=sale_id).quantity, D('0.700'))
        self.assertEqual(ledger.on_hand(self.rice), D('24.800'))
        pulled = client_for(self.seller).get('/api/sync/pull/', {'entity_types': 'product'}, HTTP_X_DEVICE_ID=str(self.till.id)).json()
        flags = {o['payload']['name']: o['payload']['sold_by_weight'] for o in pulled['operations']}
        self.assertEqual(flags, {'Rice (loose)': True, 'Milk tin': False})


class WeighedStockTests(WeightBase):
    def test_damage_count_return_and_transfer_take_kg(self):
        owner = client_for(self.ceo)
        self.assertEqual(owner.post(f'/api/inventory/items/{self.rice.id}/damage/', {'quantity': '0.350', 'reason': 'Wet'}, format='json').status_code, 201)
        self.assertEqual(owner.post(f'/api/inventory/items/{self.rice.id}/stock-count/', {'counted_quantity': '24.000', 'reason': 'Weighed'}, format='json').status_code, 201)
        self.assertEqual(ledger.on_hand(self.rice), D('24.000'))
        self.assertEqual(owner.post(f'/api/inventory/items/{self.tin.id}/damage/', {'quantity': '0.5', 'reason': 'x'}, format='json').status_code, 400)

        sale = self.sell(self.rice, 2).json()
        r = owner.post(f"/api/sales/{sale['id']}/return/", {'reason': 'Too much', 'lines': [{'sale_item': sale['items'][0]['id'], 'quantity': '0.5'}]}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(D(r.json()['refund_amount']), D('750.00'))
        self.assertEqual(ledger.on_hand(self.rice), D('22.500'))
        self.assertEqual(ledger.drift(self.shop), [])

    def test_receiving_whole_unit_stock_in_fractions_is_refused(self):
        r = client_for(self.ceo).post(f'/api/inventory/items/{self.tin.id}/batches/', {'batch_number': 'X', 'quantity_received': '2.5',
                                                                                      'cost_price': 1, 'selling_price': 1}, format='json')
        self.assertEqual(r.status_code, 400)
