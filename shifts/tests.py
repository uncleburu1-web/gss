"""Stage 3: shifts, cash movements and Z-reports uploaded from the tills."""
import json
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import RolePermission
from devices.models import Device
from sales.models import Sale
from staff.models import Worker
from sync.test_stage1 import push_from
from sync.tests import client_for, make_op, make_shop_user

from .models import CashMovement, Shift

D = Decimal


def shift_payload(owner, status='open', **extra):
    payload = {'owner': owner, 'status': status, 'opened_at': timezone.now().isoformat(), 'opening_float': 5000}
    if status == 'closed':
        report = {'cash': {'expected': 12000, 'counted': 11500, 'variance': -500}, 'sales': {'count': 3}}
        payload.update({'closed_at': timezone.now().isoformat(), 'expected_cash': 12000, 'counted_cash': 11500,
                        'variance': -500, 'z_number': 7, 'notes': 'short', 'report_json': json.dumps(report)})
    payload.update(extra)
    return payload


class ShiftSyncTests(TestCase):
    def setUp(self):
        self.seller, self.shop = make_shop_user('ada', 'Shop S')
        self.till = Device.objects.create(id=uuid.uuid4(), shop=self.shop, device_type='desktop', name='Till 1')
        self.client_ = client_for(self.seller)

    def push(self, *ops):
        response, results = push_from(self.client_, self.till, *ops)
        self.assertEqual(response.status_code, 200)
        return results

    def test_open_then_close_lands_with_its_frozen_z_report(self):
        shift_id = str(uuid.uuid4())
        self.push(make_op('shift', 'create', shift_payload('ada'), entity_id=shift_id))
        shift = Shift.objects.get(id=shift_id)
        self.assertEqual((shift.status, shift.user, shift.device), ('open', self.seller, self.till))

        self.push(make_op('shift', 'update', shift_payload('ada', 'closed'), entity_id=shift_id))
        shift.refresh_from_db()
        self.assertEqual(shift.status, 'closed')
        self.assertEqual((shift.counted_cash, shift.variance, shift.z_number), (D('11500'), D('-500'), 7))
        self.assertEqual(shift.report['cash']['variance'], -500)

    def test_a_closed_shift_never_changes_again(self):
        shift_id = str(uuid.uuid4())
        self.push(make_op('shift', 'create', shift_payload('ada', 'closed'), entity_id=shift_id))
        self.push(make_op('shift', 'update', shift_payload('ada', 'closed', counted_cash=99999, variance=0), entity_id=shift_id))
        self.assertEqual(Shift.objects.get(id=shift_id).counted_cash, D('11500'))

    def test_a_close_that_arrives_first_still_lands(self):
        shift_id = str(uuid.uuid4())
        self.push(make_op('shift', 'update', shift_payload('ada', 'closed'), entity_id=shift_id))
        self.assertEqual(Shift.objects.get(id=shift_id).status, 'closed')

    def test_cash_movements_wait_for_their_shift_then_land_once(self):
        shift_id = str(uuid.uuid4())
        move = make_op('cash_movement', 'create', {'shift_id': shift_id, 'kind': 'paid_out', 'amount': 1500,
                                                    'reason': 'Fuel for generator', 'created_at': timezone.now().isoformat()})
        self.assertNotIn(move['id'], self.push(move), 'waits for its shift')
        self.push(make_op('shift', 'create', shift_payload('ada'), entity_id=shift_id))
        self.assertEqual(self.push(move)[move['id']]['status'], 'applied')
        resent = dict(move, id=str(uuid.uuid4()), entity_id=move['entity_id'])   # same movement, new op id
        self.push(resent)
        movement = CashMovement.objects.get()
        self.assertEqual((movement.kind, movement.amount, movement.reason), ('paid_out', D('1500'), 'Fuel for generator'))

    def test_a_sale_remembers_its_shift_even_if_the_shift_comes_later(self):
        shift_id = str(uuid.uuid4())
        sale_id = str(uuid.uuid4())
        self.push(make_op('sale', 'create', {'customer_name': 'W', 'invoice_number': 1, 'shift_id': shift_id}, entity_id=sale_id))
        self.assertEqual(str(Sale.objects.get(id=sale_id).shift_ref), shift_id)

    def test_the_cloud_announces_protocol_3(self):
        self.assertEqual(self.client_.get('/api/sync/pull/', {'entity_types': 'customer'}).json()['sync_protocol'], 3)


class ShiftApiVisibilityTests(TestCase):
    """Sellers see their own shifts; owners, managers, CEOs and roles granted view_all_sales see everyone's."""

    def setUp(self):
        self.seller, self.shop = make_shop_user('bola', 'Shop V')
        other = User.objects.create_user(username='chidi', password='x')
        Worker.objects.create(user=other, full_name='Chidi', role='seller', shop=self.shop)
        self.owner = User.objects.create_user(username='boss', password='x')
        Worker.objects.create(user=self.owner, full_name='Boss', role='owner', shop=self.shop)
        now = timezone.now()
        self.mine = Shift.objects.create(shop=self.shop, owner_username='bola', user=self.seller, opened_at=now)
        self.theirs = Shift.objects.create(shop=self.shop, owner_username='chidi', user=other, opened_at=now)

    def ids(self, user):
        body = client_for(user).get('/api/shifts/').json()
        return {row['id'] for row in body['results']}

    def test_a_seller_sees_only_their_own(self):
        self.assertEqual(self.ids(self.seller), {str(self.mine.id)})
        self.assertEqual(client_for(self.seller).get(f'/api/shifts/{self.theirs.id}/').status_code, 404)

    def test_an_owner_sees_everyone(self):
        self.assertEqual(self.ids(self.owner), {str(self.mine.id), str(self.theirs.id)})

    def test_a_role_granted_view_all_sales_sees_everyone(self):
        RolePermission.objects.create(organization=self.shop.organization, role='seller', capability='view_all_sales', allowed=True)
        self.assertEqual(self.ids(self.seller), {str(self.mine.id), str(self.theirs.id)})

    def test_detail_includes_the_frozen_report_and_movements(self):
        CashMovement.objects.create(shop=self.shop, shift=self.mine, kind='paid_in', amount=200, reason='Change', occurred_at=timezone.now())
        body = client_for(self.seller).get(f'/api/shifts/{self.mine.id}/').json()
        self.assertEqual(len(body['cash_movements']), 1)
        self.assertIn('report', body)
