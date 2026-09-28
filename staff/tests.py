from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Shop, RolePermission
from .models import Worker, AttendanceRecord


def make_worker(username, role, org=None, shop=None):
    user = User.objects.create_user(username=username, password='x')
    if org is None:
        org = Organization.objects.create(name=f'{username} Shop', owner=None, business_type='general')
    if shop is None:
        shop = Shop.objects.filter(organization=org).first() or Shop.objects.create(name=org.name, organization=org)
    worker = Worker.objects.create(user=user, full_name=username, role=role, shop=shop)
    return user, worker, org, shop


class AttendanceMarkPermissionTests(TestCase):
    def setUp(self):
        self.owner, self.owner_worker, self.org, self.shop = make_worker('owner1', 'owner')
        self.reception, self.reception_worker, _, _ = make_worker('reception1', 'reception', org=self.org, shop=self.shop)
        self.seller, self.seller_worker, _, _ = make_worker('seller1', 'seller', org=self.org, shop=self.shop)

    def test_reception_can_mark_attendance(self):
        client = APIClient()
        client.force_authenticate(user=self.reception)
        res = client.post('/api/attendance/mark/', {
            'worker': str(self.seller_worker.id), 'status': 'present',
        }, format='json')
        self.assertEqual(res.status_code, 200)
        record = AttendanceRecord.objects.get(worker=self.seller_worker)
        self.assertEqual(record.status, 'present')
        self.assertEqual(record.marked_by_id, self.reception_worker.id)

    def test_seller_cannot_mark_attendance_by_default(self):
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/attendance/mark/', {
            'worker': str(self.reception_worker.id), 'status': 'present',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_owner_can_always_mark_attendance(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.post('/api/attendance/mark/', {
            'worker': str(self.seller_worker.id), 'status': 'late',
        }, format='json')
        self.assertEqual(res.status_code, 200)

    def test_marking_twice_in_one_day_updates_not_duplicates(self):
        client = APIClient()
        client.force_authenticate(user=self.reception)
        client.post('/api/attendance/mark/', {'worker': str(self.seller_worker.id), 'status': 'absent'}, format='json')
        res = client.post('/api/attendance/mark/', {'worker': str(self.seller_worker.id), 'status': 'present'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AttendanceRecord.objects.filter(worker=self.seller_worker).count(), 1)
        self.assertEqual(AttendanceRecord.objects.get(worker=self.seller_worker).status, 'present')

    def test_cannot_mark_a_worker_from_another_branch(self):
        _, other_worker, _, _ = make_worker('seller2', 'seller')
        client = APIClient()
        client.force_authenticate(user=self.reception)
        res = client.post('/api/attendance/mark/', {'worker': str(other_worker.id), 'status': 'present'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_control_center_can_grant_seller_mark_attendance(self):
        RolePermission.objects.create(organization=self.org, role='seller', capability='mark_attendance', allowed=True)
        client = APIClient()
        client.force_authenticate(user=self.seller)
        res = client.post('/api/attendance/mark/', {'worker': str(self.reception_worker.id), 'status': 'present'}, format='json')
        self.assertEqual(res.status_code, 200)


class AttendanceViewPermissionTests(TestCase):
    def setUp(self):
        self.owner, self.owner_worker, self.org, self.shop = make_worker('owner2', 'owner')
        self.reception, self.reception_worker, _, _ = make_worker('reception2', 'reception', org=self.org, shop=self.shop)
        self.seller_a, self.worker_a, _, _ = make_worker('sellerA', 'seller', org=self.org, shop=self.shop)
        self.seller_b, self.worker_b, _, _ = make_worker('sellerB', 'seller', org=self.org, shop=self.shop)
        AttendanceRecord.objects.create(shop=self.shop, worker=self.worker_a, date='2026-09-27', status='present')
        AttendanceRecord.objects.create(shop=self.shop, worker=self.worker_b, date='2026-09-27', status='absent')

    def test_reception_sees_every_worker_attendance(self):
        client = APIClient()
        client.force_authenticate(user=self.reception)
        res = client.get('/api/attendance/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']) if 'results' in res.data else len(res.data), 2)

    def test_seller_sees_only_their_own_attendance(self):
        # No 'view_attendance' capability by default -- a seller can
        # still see their OWN attendance history (self-service
        # transparency), just not their coworkers'.
        client = APIClient()
        client.force_authenticate(user=self.seller_a)
        res = client.get('/api/attendance/')
        self.assertEqual(res.status_code, 200)
        rows = res.data['results'] if 'results' in res.data else res.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]['worker']), str(self.worker_a.id))

    def test_todays_roster_includes_unmarked_active_workers(self):
        from django.utils import timezone
        AttendanceRecord.objects.all().delete()
        client = APIClient()
        client.force_authenticate(user=self.reception)
        res = client.get('/api/attendance/today/')
        self.assertEqual(res.status_code, 200)
        names = {row['worker_name'] for row in res.data['rows']}
        # owner2 is also an active worker at this same shop (see setUp),
        # so the roster includes all four.
        self.assertEqual(names, {'owner2', 'reception2', 'sellerA', 'sellerB'})
        self.assertTrue(all(row['status'] is None for row in res.data['rows']))

    def test_seller_cannot_view_todays_roster_by_default(self):
        client = APIClient()
        client.force_authenticate(user=self.seller_a)
        res = client.get('/api/attendance/today/')
        self.assertEqual(res.status_code, 403)
