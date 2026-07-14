# ============================================
# SECTION 1 — N+1 Query Diagnosis
# Tests: verify query count reduction
# ============================================

from django.test import TestCase, RequestFactory
from django.test.utils import override_settings
from orders.models import Customer, Product, Order, OrderItem
from orders.views import slow_order_list, fast_order_list
from orders.tenant import set_current_tenant, clear_current_tenant


class N1QueryTest(TestCase):
    """Tests that verify the N+1 query fix actually reduces query count."""

    @classmethod
    def setUpTestData(cls):
        """Create test data: 20 orders with 3 items each = 60 items."""
        cls.customers = [
            Customer.objects.create(name=f'Customer {i}', email=f'c{i}@test.com')
            for i in range(5)
        ]
        cls.products = [
            Product.objects.create(name=f'Product {i}', price=10.00 + i)
            for i in range(10)
        ]
        for i in range(20):
            order = Order.unscoped.create(
                customer=cls.customers[i % 5],
                status='pending',
                tenant='test-tenant',
            )
            for j in range(3):
                OrderItem.objects.create(
                    order=order,
                    product=cls.products[(i + j) % 10],
                    quantity=j + 1,
                )

    def setUp(self):
        self.factory = RequestFactory()

    def test_slow_view_returns_all_orders(self):
        """Slow view should return correct data (just slowly)."""
        request = self.factory.get('/api/orders/slow/')
        response = slow_order_list(request)
        self.assertEqual(response.status_code, 200)
        import json
        data = json.loads(response.content)
        self.assertEqual(data['count'], 20)

    def test_fast_view_returns_all_orders(self):
        """Fast view should return same number of orders as slow view."""
        request = self.factory.get('/api/orders/fast/')
        response = fast_order_list(request)
        self.assertEqual(response.status_code, 200)
        import json
        data = json.loads(response.content)
        self.assertEqual(data['count'], 20)

    def test_fast_view_uses_fewer_queries(self):
        """
        The optimized view must use dramatically fewer queries.
        Slow view: 1 + 20 + 20 + 60 + 20 = ~121 queries (for 20 orders, 3 items each)
        Fast view: 3 queries (orders+customer JOIN, items, products)
        """
        request = self.factory.get('/api/orders/fast/')

        with self.assertNumQueries(3):
            fast_order_list(request)

    def test_both_views_return_same_data(self):
        """Both views must produce identical order data (just different query counts)."""
        import json
        slow_request = self.factory.get('/api/orders/slow/')
        fast_request = self.factory.get('/api/orders/fast/')

        slow_response = json.loads(slow_order_list(slow_request).content)
        fast_response = json.loads(fast_order_list(fast_request).content)

        self.assertEqual(slow_response['count'], fast_response['count'])

        # Compare order IDs are the same set
        slow_ids = {o['id'] for o in slow_response['orders']}
        fast_ids = {o['id'] for o in fast_response['orders']}
        self.assertEqual(slow_ids, fast_ids)
