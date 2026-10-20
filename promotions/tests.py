"""Stage 10: promotions."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.utils.dateparse import parse_datetime

from audit.models import AuditLog
from audit.tests import make_org
from devices.models import Device
from inventory.models import InventoryItem, StockBatch
from sales.models import SaleItem
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op

from .engine import best_discounts
from .models import Promotion

def rows(response):
    body = response.json()
    return body.get('results', body) if isinstance(body, dict) else body


VECTORS = json.loads((Path(__file__).parent / 'test_vectors.json').read_text())


class EngineVectorTests(TestCase):
    def test_every_shared_case(self):
        now = parse_datetime(VECTORS['now'])
        for case in VECTORS['cases']:
            with self.subTest(case['name']):
                got = best_discounts(case['lines'], case['promotions'], now)
                self.assertEqual([{'discount': str(g['discount']), 'promotion_id': g['promotion_id']} for g in got], case['expected'])


class PromotionApiTests(TestCase):
    def setUp(self):
        self.org, self.shop, self.ceo, self.seller = make_org()
        self.item = InventoryItem.objects.create(shop=self.shop, name='Malt', sell_price=500, category='beverage')
        StockBatch.objects.create(item=self.item, shop=self.shop, batch_number='B', quantity_received=40,
                                  quantity_remaining=40, cost_price=300, selling_price=500)
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop')

    def create(self, user=None, **body):
        body = {'name': 'Malt 10% off', 'kind': 'percent_off', 'value': '10', 'applies_to': 'item', 'item': str(self.item.id), **body}
        return client_for(user or self.ceo).post('/api/promotions/', body, format='json')

    def test_owner_creates_sellers_can_only_read(self):
        self.assertEqual(self.create(user=self.seller).status_code, 403)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(len(rows(client_for(self.seller).get('/api/promotions/'))), 1)
        self.assertTrue(AuditLog.objects.filter(action='promotion.created').exists())

    def test_rules_are_validated(self):
        self.assertEqual(self.create(value='150').status_code, 400)
        self.assertEqual(self.create(kind='buy_x_get_y', value='0').status_code, 400)
        self.assertEqual(self.create(applies_to='category', item=None).status_code, 400)
        self.assertEqual(self.create(kind='buy_x_get_y', value='0', buy_quantity=2, get_quantity=1).status_code, 201)

    def test_active_filter_hides_stopped_and_expired(self):
        self.create()
        self.create(name='Stopped', is_active=False)
        self.create(name='Old', ends_at='2020-01-01T00:00:00Z', starts_at='2019-01-01T00:00:00Z')
        names = [p['name'] for p in rows(client_for(self.seller).get('/api/promotions/', {'active': 1}))]
        self.assertEqual(names, ['Malt 10% off'])

    def test_tills_download_promotions_and_sales_record_which_one_priced_the_line(self):
        promo_id = self.create().json()['id']
        pulled = client_for(self.seller).get('/api/sync/pull/', {'entity_types': 'promotion'}, HTTP_X_DEVICE_ID=str(self.till.id)).json()
        self.assertEqual(pulled['operations'][0]['payload']['item_id'], str(self.item.id))

        sale_id, line_id = str(uuid.uuid4()), str(uuid.uuid4())
        op = make_op('sale_doc', 'create', {
            'id': sale_id, 'status': 'completed', 'invoice_number': 1,
            'items': [{'id': line_id, 'product_id': str(self.item.id), 'item_name': 'Malt', 'quantity': 4,
                       'unit_price': 500, 'discount': 200, 'promotion_id': promo_id}],
            'payments': [{'id': str(uuid.uuid4()), 'method': 'cash', 'amount': 1800}],
        }, entity_id=sale_id)
        push_from(client_for(self.seller), self.till, op)
        line = SaleItem.objects.get(id=line_id)
        self.assertEqual((str(line.promotion_id), line.discount), (promo_id, Decimal('200.00')))
        self.assertFalse(AuditLog.objects.filter(action='sale.discount').exists(), 'promotions are not per-sale audit noise')
        row = rows(client_for(self.ceo).get('/api/promotions/'))[0]
        self.assertEqual((row['times_used'], row['discount_given']), (1, '200.00'))


class WebSalePromotionTests(PromotionApiTests):
    def test_a_web_sale_keeps_the_promotion_and_rejects_impossible_discounts(self):
        promo_id = self.create().json()['id']
        body = lambda discount: {'customer_name': 'Walk-in', 'payment_method': 'cash', 'status': 'completed',
                                 'items': [{'item': str(self.item.id), 'item_name': 'Malt', 'quantity': 2, 'unit_price': '500',
                                            'discount': discount, 'promotion': promo_id}]}
        ok = client_for(self.seller).post('/api/sales/', body('100.00'), format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(Decimal(str(ok.json()['total'])), Decimal('900'))
        self.assertEqual(SaleItem.objects.get(sale_id=ok.json()['id']).promotion_id, uuid.UUID(promo_id))
        self.assertEqual(client_for(self.seller).post('/api/sales/', body('5000'), format='json').status_code, 400)
