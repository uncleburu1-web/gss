"""
Duplicate-sale protection for the web and Android apps (audit finding D-22).

If a sale request reaches the server but the response is lost (a dropped connection, a phone
going through a tunnel), the cashier taps Charge again. Without an Idempotency-Key that rings the
customer up twice and deducts stock twice. With one, the retry gets the FIRST response back.

    python manage.py test sales.test_idempotency
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import IdempotencyRecord, Organization, Shop
from staff.models import Worker
from .models import Sale

KEY = 'charge-0001-aaaa'


def make_seller(username='cashier1'):
    user = User.objects.create_user(username=username, password='x')
    org = Organization.objects.create(name=f'{username} Shop', owner=None, business_type='general')
    shop = Shop.objects.create(name=f'{username} Shop', organization=org)
    Worker.objects.create(user=user, full_name=username, role='seller', shop=shop)
    return user, shop


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def sale_body(price='500.00', **extra):
    body = {
        'customer_name': 'Walk-in', 'payment_method': 'cash', 'status': 'completed',
        'items': [{'item_name': 'Repair labour', 'category': 'service', 'quantity': 1, 'unit_price': price, 'unit_cost': '0'}],
    }
    body.update(extra)
    return body


class CreateSaleIdempotencyTests(TestCase):
    def setUp(self):
        self.user, self.shop = make_seller()
        self.client_ = client_for(self.user)

    def post(self, body=None, key=KEY, client=None):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.client_).post('/api/sales/', body or sale_body(), format='json', **headers)

    def test_retrying_with_the_same_key_returns_the_first_sale_and_creates_no_second_one(self):
        first = self.post()
        retry = self.post()

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(retry.json()['id'], first.json()['id'])
        self.assertEqual(retry['Idempotent-Replay'], 'true')
        self.assertNotIn('Idempotent-Replay', first)
        self.assertEqual(Sale.objects.filter(shop=self.shop).count(), 1)

    def test_without_a_key_nothing_changes_each_request_is_its_own_sale(self):
        self.post(key=None)
        self.post(key=None)
        self.assertEqual(Sale.objects.filter(shop=self.shop).count(), 2)

    def test_reusing_a_key_for_a_different_sale_is_refused_not_silently_swallowed(self):
        self.post(sale_body('500.00'))
        clash = self.post(sale_body('900.00'))

        self.assertEqual(clash.status_code, 422)
        self.assertEqual(Sale.objects.filter(shop=self.shop).count(), 1)

    def test_a_request_that_failed_is_not_remembered_so_the_corrected_retry_can_succeed(self):
        bad = sale_body()
        bad['items'][0]['quantity'] = 0                       # invalid
        failed = self.post(bad)
        self.assertGreaterEqual(failed.status_code, 400)
        self.assertFalse(IdempotencyRecord.objects.filter(user=self.user, key=KEY).exists())

        ok = self.post(sale_body())                           # same key, now valid
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(Sale.objects.filter(shop=self.shop).count(), 1)

    def test_two_cashiers_may_use_the_same_key_independently(self):
        other, other_shop = make_seller('cashier2')
        self.post()
        self.post(client=client_for(other))
        self.assertEqual(Sale.objects.filter(shop=self.shop).count(), 1)
        self.assertEqual(Sale.objects.filter(shop=other_shop).count(), 1)

    def test_a_malformed_key_is_rejected_up_front(self):
        res = self.post(key='no spaces allowed!')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)


class AddPaymentIdempotencyTests(TestCase):
    def setUp(self):
        self.user, self.shop = make_seller()
        self.client_ = client_for(self.user)
        res = self.client_.post('/api/sales/', sale_body('1000.00', status='outstanding', amount_paid='100.00'), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.sale_id = res.json()['id']

    def pay(self, amount, key='pay-0001-aaaaaa'):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return self.client_.post(f'/api/sales/{self.sale_id}/add-payment/', {'amount': amount}, format='json', **headers)

    def test_a_retried_installment_payment_is_counted_once(self):
        self.pay('200.00')
        retry = self.pay('200.00')

        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(Sale.objects.get(id=self.sale_id).amount_paid, 300)     # 100 + 200, NOT 100 + 200 + 200

    def test_without_a_key_payments_still_add_up_as_before(self):
        self.pay('200.00', key=None)
        self.pay('200.00', key=None)
        self.assertEqual(Sale.objects.get(id=self.sale_id).amount_paid, 500)
