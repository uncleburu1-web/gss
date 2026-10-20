"""Stage 9: moving stock between branches."""
import uuid

from django.contrib.auth.models import User
from django.test import TestCase

from audit.models import AuditLog
from core.models import Organization, Shop
from devices.models import Device
from inventory import ledger
from inventory.models import InventoryItem, StockBatch, StockMovement
from staff.models import Worker
from sync.tests import client_for

from .models import ItemVariant


class TransferTests(TestCase):
    def setUp(self):
        self.ceo = User.objects.create_user(username='ceo', password='x')
        self.org = Organization.objects.create(name='Chain', owner=self.ceo, business_type='general')
        self.a = Shop.objects.create(name='Ikeja', organization=self.org)
        self.b = Shop.objects.create(name='Lekki', organization=self.org)
        Worker.objects.create(user=self.ceo, full_name='CEO', role='owner', shop=self.a)
        self.item = InventoryItem.objects.create(shop=self.a, name='Milk Tin', barcode='123', sell_price=900)
        for n, (qty, exp) in enumerate([(4, '2027-01-01'), (10, '2027-06-01')]):
            b = StockBatch.objects.create(item=self.item, shop=self.a, batch_number=f'A{n}', quantity_received=qty,
                                          quantity_remaining=qty, cost_price=600 + n * 50, selling_price=900, expiry_date=exp)
            StockMovement.objects.create(shop=self.a, item=self.item, batch=b, kind='opening', quantity=qty)

    def move(self, user=None, **body):
        body.setdefault('from_branch', str(self.a.id))
        body.setdefault('to_branch', str(self.b.id))
        body.setdefault('lines', [{'item': str(self.item.id), 'quantity': 6}])
        return client_for(user or self.ceo).post('/api/inventory/transfers/', body, format='json')

    def test_stock_moves_earliest_expiry_first_and_keeps_cost_and_expiry(self):
        response = self.move(note='Lekki ran out')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ledger.on_hand(self.item), 8)
        copy = InventoryItem.objects.get(shop=self.b)
        self.assertEqual((copy.name, copy.barcode, copy.sell_price), ('Milk Tin', '123', self.item.sell_price))
        batches = sorted(StockBatch.objects.filter(item=copy), key=lambda b: str(b.expiry_date))
        self.assertEqual([(b.quantity_remaining, str(b.expiry_date), int(b.cost_price)) for b in batches],
                         [(4, '2027-01-01', 600), (2, '2027-06-01', 650)])
        self.assertEqual(ledger.drift(), [])
        self.assertEqual(AuditLog.objects.filter(action='stock.transfer').count(), 2)

    def test_an_existing_product_at_the_destination_is_reused(self):
        existing = InventoryItem.objects.create(shop=self.b, name='milk tin', sell_price=950)
        self.move()
        self.assertEqual(InventoryItem.objects.filter(shop=self.b).count(), 1)
        self.assertEqual(ledger.on_hand(existing), 6)

    def test_cannot_move_more_than_there_is_and_nothing_half_moves(self):
        response = self.move(lines=[{'item': str(self.item.id), 'quantity': 3}, {'item': str(self.item.id), 'quantity': 50}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ledger.on_hand(self.item), 14)
        self.assertFalse(InventoryItem.objects.filter(shop=self.b).exists())

    def test_only_the_ceo_can_move_stock_and_only_within_the_business(self):
        manager = User.objects.create_user(username='mgr', password='x')
        Worker.objects.create(user=manager, full_name='Mgr', role='branch_manager', shop=self.a)
        self.assertEqual(self.move(user=manager).status_code, 403)
        other = Shop.objects.create(name='Elsewhere', organization=Organization.objects.create(name='Other', business_type='general'))
        self.assertEqual(self.move(to_branch=str(other.id)).status_code, 400)

    def test_variants_move_to_the_same_size_and_colour(self):
        item = InventoryItem.objects.create(shop=self.a, name='Polo', sell_price=5000)
        v = ItemVariant.objects.create(shop=self.a, item=item, size='M', color='Blue')
        StockBatch.objects.create(item=item, variant=v, shop=self.a, batch_number='P', quantity_received=5, quantity_remaining=5, cost_price=3000, selling_price=5000)
        self.assertEqual(self.move(lines=[{'item': str(item.id), 'quantity': 1}]).status_code, 400)
        self.assertEqual(self.move(lines=[{'item': str(item.id), 'variant': str(v.id), 'quantity': 2}]).status_code, 201)
        moved = ItemVariant.objects.get(item__shop=self.b)
        self.assertEqual((moved.size, moved.color, moved.quantity), ('M', 'Blue', 2))

    def test_source_tills_pull_the_move_out_and_destination_tills_get_batches_not_movements(self):
        self.move()
        till_a = Device.objects.create(id=uuid.uuid4(), shop=self.a, device_type='desktop')
        till_b = Device.objects.create(id=uuid.uuid4(), shop=self.b, device_type='desktop')
        pull = lambda till, types: client_for(self.ceo).get('/api/sync/pull/', {'entity_types': types}, HTTP_X_DEVICE_ID=str(till.id)).json()['operations']
        out = pull(till_a, 'stock_movement')
        self.assertEqual(sum(float(o['payload']['quantity']) for o in out), -6)
        self.assertEqual(pull(till_b, 'stock_movement'), [])
        self.assertEqual(sum(o['payload']['quantity_received'] for o in pull(till_b, 'stock_batch')), 6)

    def test_list_shows_past_moves(self):
        self.move(note='Weekend top-up')
        rows = client_for(self.ceo).get('/api/inventory/transfers/').json()
        self.assertEqual((rows[0]['from_branch'], rows[0]['to_branch'], rows[0]['lines'][0]['quantity']), ('Ikeja', 'Lekki', 6))
