"""Stage 1b: paged pull (G11)."""
from django.test import TestCase

from customers.models import Customer
from inventory.models import InventoryItem

from .tests import client_for, make_shop_user


class PagedPullTests(TestCase):
    def setUp(self):
        self.user, self.shop = make_shop_user('page', 'Paged Shop')
        self.client_ = client_for(self.user)
        for i in range(7):
            InventoryItem.objects.create(shop=self.shop, name=f'P{i}', sell_price=1)
        for i in range(5):
            Customer.objects.create(shop=self.shop, name=f'C{i}')

    def pull_all(self, limit):
        seen, cursor, pages = [], None, 0
        while True:
            params = {'limit': limit, 'entity_types': 'product,customer'}
            if cursor:
                params['cursor'] = cursor
            body = self.client_.get('/api/sync/pull/', params).json()
            pages += 1
            seen += [(op['entity_type'], op['entity_id']) for op in body['operations']]
            self.assertLessEqual(len(body['operations']), limit)
            if not body['has_more']:
                return seen, pages
            cursor = body['next_cursor']
            self.assertLess(pages, 50, 'paging never ended')

    def test_every_row_arrives_exactly_once_across_pages(self):
        for limit in (1, 3, 5, 7, 12, 100):
            seen, pages = self.pull_all(limit)
            self.assertEqual(len(seen), 12, f'limit {limit}')
            self.assertEqual(len(set(seen)), 12, f'limit {limit}: duplicates')
            self.assertGreaterEqual(pages, -(-12 // limit))

    def test_parents_come_before_children(self):
        seen, _ = self.pull_all(4)
        types = [t for t, _ in seen]
        self.assertEqual(types, sorted(types, key=['product', 'customer'].index))

    def test_without_limit_the_old_single_response_is_unchanged(self):
        body = self.client_.get('/api/sync/pull/', {'entity_types': 'product,customer'}).json()
        self.assertEqual(len(body['operations']), 12)
        self.assertFalse(body['has_more'])
        self.assertIsNone(body['next_cursor'])

    def test_a_garbage_cursor_starts_from_the_beginning(self):
        body = self.client_.get('/api/sync/pull/', {'limit': 100, 'cursor': 'nonsense', 'entity_types': 'product'}).json()
        self.assertEqual(len(body['operations']), 7)
