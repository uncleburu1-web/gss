"""Finished restaurant orders arriving from the hub (REST-08, sync protocol 6): stored once, checked
against the document's own frozen pricing, only from the branch's hub, and counted in the existing
reports through a Sale that moves no stock."""
import copy
import uuid
from decimal import Decimal

from django.test import TestCase

from audit.models import AuditLog
from devices.models import Device
from inventory.models import StockMovement
from sales.models import Sale, SaleAllocation, SaleItem

from .billing import compute_bill
from .models import (
    Bill, DiningArea, KitchenStation, MenuAvailability, MenuCategory, MenuItem, ModifierGroup, ModifierOption, Order, RestaurantTable, TaxRule,
)
from .tests import client_for, make_business, turn_on_restaurant, worker
from .views import settings_for

U = lambda: str(uuid.uuid4())  # noqa: E731


class Restaurant:
    """A restaurant branch with a hub, a menu and VAT, as the owner would set it up."""

    def __init__(self, name='Mama Put'):
        self.org, self.shop, self.ceo = make_business(name)
        turn_on_restaurant(self.shop)
        self.cashier = worker(self.shop, 'cashier')
        self.hub = Device.objects.create(shop=self.shop, device_type='desktop', name='Hub')
        s = settings_for(self.shop)
        s.hub_device, s.service_charge_rate, s.service_charge_types = self.hub, Decimal('10'), ['dine_in']
        s.save()
        self.kitchen = KitchenStation.objects.create(shop=self.shop, name='Kitchen')
        self.bar = KitchenStation.objects.create(shop=self.shop, name='Bar')
        mains = MenuCategory.objects.create(shop=self.shop, name='Mains', station=self.kitchen)
        drinks = MenuCategory.objects.create(shop=self.shop, name='Drinks', station=self.bar)
        self.jollof = MenuItem.objects.create(shop=self.shop, category=mains, name='Jollof rice', price=Decimal('3500'), tax_class='food')
        self.chapman = MenuItem.objects.create(shop=self.shop, category=drinks, name='Chapman', price=Decimal('1500'), tax_class='drink')
        size = ModifierGroup.objects.create(shop=self.shop, name='Size', min_select=1, max_select=1)
        self.large = ModifierOption.objects.create(shop=self.shop, group=size, name='Large', price_delta=Decimal('500'))
        self.t1 = RestaurantTable.objects.create(shop=self.shop, area=DiningArea.objects.create(shop=self.shop, name='Hall'), label='T1', seats=4)
        self.vat = TaxRule.objects.create(shop=self.shop, name='VAT', rate=Decimal('7.5'), valid_from='2026-01-01', is_active=True)

    def doc(self, discount=None, vat_inclusive=False, status='closed', pay=None):
        """An order_doc exactly as the hub builds it (gssdesktop restaurant/engine.js orderDoc)."""
        jollof_line, chapman_line = U(), U()
        pricing = {
            'service_charge': {'rate': '10', 'service_types': ['dine_in']},
            'tax_rules': [{'id': str(self.vat.id), 'name': 'VAT', 'rate': '7.5', 'inclusive': vat_inclusive, 'tax_classes': [],
                           'applies_to_service_charge': False, 'valid_from': '2026-01-01', 'valid_to': None, 'is_active': True}],
        }
        items = [
            {'id': jollof_line, 'menu_item_id': str(self.jollof.id), 'name': 'Jollof rice', 'unit_price': '3500.00', 'modifiers_price': '500.00',
             'tax_class': 'food', 'quantity': 2, 'voided_quantity': 0, 'state': 'served', 'waste': False, 'void_reason': None, 'notes': 'no pepper',
             'sent_at': '2026-10-21T12:01:00.000Z', 'served_at': '2026-10-21T12:20:00.000Z',
             'modifiers': [{'option_id': str(self.large.id), 'group_id': str(self.large.group_id), 'name': 'Large', 'price_delta': '500.00'}]},
            {'id': chapman_line, 'menu_item_id': str(self.chapman.id), 'name': 'Chapman', 'unit_price': '1500.00', 'modifiers_price': '0.00',
             'tax_class': 'drink', 'quantity': 1, 'voided_quantity': 0, 'state': 'served', 'waste': False, 'void_reason': None, 'notes': '',
             'sent_at': '2026-10-21T12:01:00.000Z', 'served_at': '2026-10-21T12:05:00.000Z', 'modifiers': []},
        ]
        doc = {
            'id': U(), 'number': 'K-0001', 'service_type': 'dine_in', 'table_id': str(self.t1.id), 'guests': 2, 'waiter': self.cashier.username,
            'opened_by': self.cashier.username, 'status': status, 'business_date': '2026-10-21', 'opened_at': '2026-10-21T12:00:00.000Z',
            'closed_at': '2026-10-21T12:40:00.000Z' if status == 'closed' else None, 'cancel_reason': 'Guest left' if status == 'cancelled' else None,
            'notes': '', 'pricing': pricing, 'items': items,
            'tickets': [
                {'id': U(), 'station_id': str(self.kitchen.id), 'number': 'K-1', 'kind': 'new', 'sent_at': '2026-10-21T12:01:00.000Z', 'sent_by': 'wale',
                 'items': [{'id': U(), 'order_item_id': jollof_line, 'quantity': 2, 'voided_quantity': 0, 'state': 'served',
                            'started_at': '2026-10-21T12:02:00.000Z', 'ready_at': '2026-10-21T12:18:00.000Z', 'served_at': '2026-10-21T12:20:00.000Z'}]},
                {'id': U(), 'station_id': str(self.bar.id), 'number': 'B-1', 'kind': 'new', 'sent_at': '2026-10-21T12:01:00.000Z', 'sent_by': 'wale',
                 'items': [{'id': U(), 'order_item_id': chapman_line, 'quantity': 1, 'voided_quantity': 0, 'state': 'served',
                            'started_at': None, 'ready_at': '2026-10-21T12:04:00.000Z', 'served_at': '2026-10-21T12:05:00.000Z'}]},
            ],
        }
        bill = compute_bill({
            'service_type': 'dine_in', 'business_date': '2026-10-21', 'discount': discount,
            'lines': [{'id': i['id'], 'unit_price': i['unit_price'], 'modifiers_price': i['modifiers_price'], 'quantity': str(i['quantity']),
                       'voided_quantity': str(i['voided_quantity']), 'tax_class': i['tax_class']} for i in items],
            'service_charge': pricing['service_charge'], 'tax_rules': pricing['tax_rules'],
        })
        amount = bill['total'] if pay is None else pay
        doc['bill'] = {
            'id': U(), 'number': 'B-K-0001', 'status': 'settled' if status == 'closed' else 'open', **bill, 'discount_spec': discount,
            'discount_reason': 'Regular' if discount else None,
            'payments': [] if status != 'closed' else [{
                'id': U(), 'method': 'cash', 'amount': amount, 'tendered': '12000.00', 'change': '837.50', 'tip': '0.00', 'reference': '',
                'shift_ref': U(), 'received_by': self.cashier.username, 'received_at': '2026-10-21T12:39:00.000Z'}],
        }
        return doc

    def push(self, entity_type, payload, device='hub', user=None, op_id=None, entity_id=None):
        client = client_for(user or self.ceo)
        headers = {} if device is None else {'HTTP_X_DEVICE_ID': str(self.hub.id if device == 'hub' else device.id)}
        r = client.post('/api/sync/push/', {'operations': [{
            'id': op_id or U(), 'entity_type': entity_type, 'entity_id': entity_id or payload.get('id') or payload.get('menu_item_id'),
            'operation': 'create' if entity_type != 'menu_availability' else 'update', 'payload': payload,
            'client_timestamp': '2026-10-21T12:41:00Z'}]}, format='json', **headers)
        assert r.status_code == 200, r.content
        return r.json()['results'][0]


class OrderDocTests(TestCase):
    def setUp(self):
        self.r = Restaurant()

    def test_a_closed_order_is_stored_once_and_becomes_a_sale_that_moves_no_stock(self):
        doc = self.r.doc()
        self.assertEqual(doc['bill']['total'], '11162.50')
        movements = StockMovement.objects.count()
        op = U()
        self.assertEqual(self.r.push('order_doc', doc, op_id=op)['status'], 'applied')

        order = Order.objects.get(id=doc['id'])
        self.assertEqual((order.shop, order.status, order.table, order.number), (self.r.shop, 'closed', self.r.t1, 'K-0001'))
        self.assertEqual([(i.name, i.quantity, [m.name for m in i.modifiers.all()]) for i in order.items.order_by('name')],
                         [('Chapman', 1, []), ('Jollof rice', 2, ['Large'])])
        self.assertEqual(sorted(t.station.name for t in order.tickets.all()), ['Bar', 'Kitchen'])
        self.assertEqual([(c.name, c.amount) for c in order.bill.charges.all()], [('Service charge', Decimal('950.00')), ('VAT', Decimal('712.50'))])
        self.assertEqual(order.bill.payments.get().amount, Decimal('11162.50'))

        sale = order.sale
        self.assertEqual(sale.total, Decimal('11162.50'), 'the Sale adds up to what the customer paid')
        self.assertEqual(sorted((i.item_name, i.category, i.quantity, i.unit_price) for i in sale.items.all()), [
            ('Chapman', 'restaurant', 1, Decimal('1500.00')),
            ('Jollof rice (Large)', 'restaurant', 2, Decimal('4000.00')),
            ('Service charge 10%', 'restaurant_charge', 1, Decimal('950.00')),
            ('VAT 7.5%', 'restaurant_tax', 1, Decimal('712.50')),
        ])
        self.assertEqual((sale.payment_method, sale.amount_paid, sale.worker.user, sale.device), ('cash', Decimal('11162.50'), self.r.cashier, self.r.hub))
        self.assertEqual([(p.method, p.amount) for p in sale.payments.all()], [('cash', Decimal('11162.50'))])
        self.assertTrue(all(i.item_id is None for i in sale.items.all()))
        self.assertEqual((StockMovement.objects.count(), SaleAllocation.objects.filter(sale_item__sale=sale).count()), (movements, 0),
                         'a restaurant sale never moves stock')

        self.assertEqual(self.r.push('order_doc', doc, op_id=op)['status'], 'already_applied', 'a resend after a lost reply')
        self.assertEqual(self.r.push('order_doc', doc)['status'], 'applied', 'the same order under a new operation id')
        self.assertEqual((Order.objects.count(), Sale.objects.count()), (1, 1), 'still one order, one sale')

    def test_only_the_hub_of_a_restaurant_branch_may_send_orders(self):
        other_till = Device.objects.create(shop=self.r.shop, device_type='desktop', name='Counter')
        r = self.r.push('order_doc', self.r.doc(), device=other_till)
        self.assertEqual(r['status'], 'rejected')
        self.assertTrue(r['error'].startswith('not_the_hub:'), r)
        self.assertTrue(self.r.push('order_doc', self.r.doc(), device=None)['error'].startswith('not_the_hub:'), 'no device at all')
        self.r.shop.modules.filter(module='restaurant').update(enabled=False)
        self.assertTrue(self.r.push('order_doc', self.r.doc())['error'].startswith('module_disabled:'))
        self.assertEqual(Order.objects.count(), 0)

    def test_the_bill_is_checked_from_the_document_never_from_todays_settings(self):
        # After the order, the owner changes the service charge and switches VAT off: a valid order still goes in.
        s = settings_for(self.r.shop)
        s.service_charge_rate = Decimal('15')
        s.save()
        TaxRule.objects.filter(pk=self.r.vat.pk).update(is_active=False)
        self.assertEqual(self.r.push('order_doc', self.r.doc())['status'], 'applied')

        def tampered(change):
            d = self.r.doc()
            change(d)
            return self.r.push('order_doc', d)

        cases = {
            'total changed': lambda d: d['bill'].update(total='10000.00'),
            'a charge changed': lambda d: d['bill']['charges'][1].update(amount='100.00'),
            'a price changed after billing': lambda d: d['items'][1].update(unit_price='1000.00'),
            'a charge dropped': lambda d: d['bill']['charges'].pop(),
            'a frozen rate changed': lambda d: d['pricing']['tax_rules'][0].update(rate='5'),
        }
        for label, change in cases.items():
            r = tampered(change)
            self.assertEqual(r['status'], 'rejected', label)
            self.assertTrue(r['error'].startswith('bill_mismatch:'), (label, r))
        self.assertEqual(Order.objects.count(), 1)

    def test_everything_named_must_belong_to_this_branch(self):
        other = Restaurant('Other Place')
        cases = {
            'unknown_menu_item': lambda d: d['items'][0].update(menu_item_id=str(other.jollof.id)),
            'unknown_table': lambda d: d.update(table_id=str(other.t1.id)),
            'unknown_station': lambda d: d['tickets'][0].update(station_id=str(other.kitchen.id)),
            'unknown_option': lambda d: d['items'][0]['modifiers'][0].update(option_id=str(other.large.id)),
            'unknown_tax_rule': lambda d: d['pricing']['tax_rules'][0].update(id=str(other.vat.id)),
            'bad_document': lambda d: d['items'][0].update(menu_item_id='not-an-id'),
        }
        for code, change in cases.items():
            d = self.r.doc()
            change(d)
            r = self.r.push('order_doc', d)
            self.assertTrue(r['error'].startswith(f'{code}:'), (code, r))
        self.assertEqual(Order.objects.count(), 0)

    def test_closed_means_paid_in_full_and_open_orders_are_never_sent(self):
        self.assertTrue(self.r.push('order_doc', self.r.doc(pay='5000.00'))['error'].startswith('bill_not_settled:'))
        d = self.r.doc()
        d['status'] = 'open'
        self.assertTrue(self.r.push('order_doc', d)['error'].startswith('order_open:'))

    def test_a_cancelled_order_is_kept_for_the_record_but_is_not_a_sale(self):
        d = self.r.doc(status='cancelled')
        self.assertEqual(self.r.push('order_doc', d)['status'], 'applied')
        order = Order.objects.get(id=d['id'])
        self.assertEqual((order.status, order.cancel_reason, order.sale), ('cancelled', 'Guest left', None))
        self.assertEqual(Sale.objects.count(), 0)

    def test_a_discount_is_shared_across_the_food_lines_and_the_sale_still_adds_up(self):
        d = self.r.doc(discount={'type': 'amount', 'value': '1000'})
        self.assertEqual(self.r.push('order_doc', d)['status'], 'applied')
        sale = Order.objects.get(id=d['id']).sale
        food = sale.items.filter(category='restaurant')
        self.assertEqual(sum(i.discount for i in food), Decimal('1000.00'))
        self.assertEqual(sale.total, Decimal(d['bill']['total']))
        self.assertEqual(sale.total, Bill.objects.get(order_id=d['id']).total)

    def test_a_tax_inside_the_prices_is_not_added_again(self):
        d = self.r.doc(vat_inclusive=True)
        self.assertEqual(self.r.push('order_doc', d)['status'], 'applied')
        sale = Order.objects.get(id=d['id']).sale
        self.assertFalse(sale.items.filter(category='restaurant_tax').exists())
        self.assertEqual(sale.total, Decimal(d['bill']['total']))


class RestaurantAuditAndAvailabilityTests(TestCase):
    def setUp(self):
        self.r = Restaurant()

    def audit(self, action, device='hub'):
        return self.r.push('audit_event', {'id': U(), 'action': action, 'summary': 'Voided 1 x Chapman', 'reason': 'Guest changed mind',
                                           'data': {}, 'actor': 'wale', 'created_at': '2026-10-21T12:30:00Z'}, device=device)

    def test_the_hubs_audit_entries_are_accepted_from_the_hub_only(self):
        self.assertEqual(self.audit('restaurant.item_voided')['status'], 'applied')
        entry = AuditLog.objects.get(action='restaurant.item_voided')
        self.assertEqual((entry.shop, entry.reason, entry.source), (self.r.shop, 'Guest changed mind', 'till'))
        other = Device.objects.create(shop=self.r.shop, device_type='desktop')
        self.assertTrue(self.audit('restaurant.item_voided', device=other)['error'].startswith('not_the_hub:'))
        self.assertEqual(self.audit('restaurant.made_up')['error'], 'Unknown audit action.')
        self.assertEqual(self.audit('drawer.opened', device=other)['status'], 'applied', 'supermarket till actions unchanged')

    def test_sold_out_is_recorded_for_the_day_and_the_latest_switch_wins(self):
        item = str(self.r.jollof.id)
        push = lambda sold, at: self.r.push('menu_availability', {'menu_item_id': item, 'sold_out': sold,  # noqa: E731
                                                                  'business_date': '2026-10-21', 'by': 'chef', 'at': at}, entity_id=item)
        self.assertEqual(push(True, '2026-10-21T13:00:00Z')['status'], 'applied')
        self.assertEqual(push(False, '2026-10-21T15:00:00Z')['status'], 'applied')
        self.assertEqual(push(True, '2026-10-21T14:00:00Z')['status'], 'applied', 'an older switch arriving late')
        row = MenuAvailability.objects.get()
        self.assertEqual((row.sold_out, row.at.hour), (False, 15))
        self.r.jollof.refresh_from_db()
        self.assertTrue(self.r.jollof.is_available, 'the menu itself is not changed: tomorrow it is back')


class ReportsIncludeRestaurantSalesTests(TestCase):
    def setUp(self):
        self.r = Restaurant()
        self.assertEqual(self.r.push('order_doc', self.r.doc())['status'], 'applied')
        self.client = client_for(self.r.ceo)

    def get(self, url):
        r = self.client.get(url + '?month=2026-10')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_tax_report_shows_the_vat_actually_charged_and_item_reports_show_no_charges(self):
        tax = self.get('/api/reports/tax/')
        self.assertEqual(tax['total_tax_collected'], 712.5)
        self.assertEqual([(r['tax_rate'], r['taxable_sales'], r['tax_collected']) for r in tax['rows']], [(7.5, 9500.0, 712.5)],
                         'restaurant food is not reported as zero-rated')
        self.assertEqual([(r['name'], r['tax_rate'], r['inclusive']) for r in tax['restaurant']], [('VAT', 7.5, False)])
        best = self.get('/api/reports/best-selling/')
        names = [r['item_name'] for r in (best['rows'] if isinstance(best, dict) else best)]
        self.assertIn('Jollof rice (Large)', names)
        self.assertFalse(any(n.startswith(('VAT', 'Service charge')) for n in names))
        summary = self.get('/api/reports/sales-summary/')
        self.assertEqual(float(summary['total_sales']), 11162.5)
        self.assertEqual(float(summary['gross_profit']), 10450.0, 'food and service charge; the VAT is not profit')


class RestaurantReportTests(TestCase):
    def setUp(self):
        self.r = Restaurant()
        self.closed = self.r.doc()
        self.cancelled = self.r.doc(status='cancelled')
        for d in (self.closed, self.cancelled):
            self.assertEqual(self.r.push('order_doc', d)['status'], 'applied')

    def test_the_summary_adds_up_from_the_frozen_bills(self):
        r = client_for(self.r.ceo).get('/api/reports/restaurant/summary/?from=2026-10-21&to=2026-10-21')
        self.assertEqual(r.status_code, 200, r.content)
        s = r.json()
        self.assertEqual((s['orders'], s['cancelled'], s['covers'], s['takings'], s['average_bill']), (1, 1, 2, '11162.50', '11162.50'))
        self.assertEqual(s['by_service_type'], {'dine_in': {'orders': 1, 'takings': '11162.50'}})
        self.assertEqual(s['by_payment_method'], {'cash': '11162.50'})
        self.assertEqual((s['service_charge'], s['discounts'], s['tips']), ('950.00', '0.00', '0.00'))
        self.assertEqual(s['taxes'], [{'name': 'VAT', 'rate': '7.5000', 'inclusive': False, 'base': '9500.00', 'amount': '712.50'}])
        self.assertEqual(s['kitchen_minutes'], {'Bar': {'tickets_items': 1, 'average': 3.0}, 'Kitchen': {'tickets_items': 1, 'average': 17.0}})
        self.assertEqual(s['top_items'][0], {'name': 'Jollof rice', 'quantity': '2.000', 'amount': '8000.00'})
        empty = client_for(self.r.ceo).get('/api/reports/restaurant/summary/?date=2026-10-20').json()
        self.assertEqual((empty['orders'], empty['takings']), (0, '0.00'))

    def test_who_sees_what(self):
        ceo = client_for(self.r.ceo)
        listed = ceo.get('/api/restaurant/orders/?from=2026-10-21&to=2026-10-21').json()['results']
        self.assertEqual(sorted(o['status'] for o in listed), ['cancelled', 'closed'])
        detail = ceo.get(f'/api/restaurant/orders/{self.closed["id"]}/').json()
        self.assertEqual((detail['bill']['total'], len(detail['items']), len(detail['tickets'])), ('11162.50', 2, 2))

        # The cashier took these orders: sees them, but not the money summary unless allowed.
        mine = client_for(self.r.cashier).get('/api/restaurant/orders/?date=2026-10-21').json()['results']
        self.assertEqual(len(mine), 2)
        stranger = worker(self.r.shop, 'waiter')
        self.assertEqual(client_for(stranger).get('/api/restaurant/orders/?date=2026-10-21').json()['results'], [], 'a waiter sees only their own')
        self.assertEqual(client_for(stranger).get(f'/api/restaurant/orders/{self.closed["id"]}/').status_code, 404)
        self.assertEqual(client_for(stranger).get('/api/reports/restaurant/summary/').status_code, 403)

        other = Restaurant('Elsewhere')
        self.assertEqual(client_for(other.ceo).get(f'/api/restaurant/orders/{self.closed["id"]}/').status_code, 404, "another business's order")
        self.r.shop.modules.filter(module='restaurant').update(enabled=False)
        self.assertEqual(ceo.get('/api/restaurant/orders/').json()['code'], 'module_disabled')
        self.assertEqual(ceo.get('/api/restaurant/orders/?from=2026-10-22&to=2026-10-21').status_code, 403, 'module check comes first')
