# ============================================
# SECTION 3 — Multi-Tenant Data Isolation
# Tests: prove tenant isolation (including negative cases)
# ============================================

from django.test import TestCase, RequestFactory
from orders.models import Customer, Product, Order, OrderItem
from orders.tenant import set_current_tenant, get_current_tenant, clear_current_tenant
from orders.middleware import TenantMiddleware


class TenantIsolationTest(TestCase):
    """
    Tests that prove multi-tenant data isolation works correctly.
    Includes NEGATIVE tests — tenant A cannot see tenant B's data.
    """

    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(name='Shared Customer', email='shared@test.com')
        cls.product = Product.objects.create(name='Test Product', price=25.00)

        # Create orders for two different tenants
        for i in range(5):
            Order.unscoped.create(
                customer=cls.customer,
                status='pending',
                tenant='tenant-a',
            )
        for i in range(3):
            Order.unscoped.create(
                customer=cls.customer,
                status='shipped',
                tenant='tenant-b',
            )

    def tearDown(self):
        """Always clear tenant after each test to prevent leaking."""
        clear_current_tenant()

    # --- Positive tests ---

    def test_tenant_a_sees_only_own_orders(self):
        """Tenant A should see exactly 5 orders."""
        set_current_tenant('tenant-a')
        orders = Order.objects.all()
        self.assertEqual(orders.count(), 5)
        for order in orders:
            self.assertEqual(order.tenant, 'tenant-a')

    def test_tenant_b_sees_only_own_orders(self):
        """Tenant B should see exactly 3 orders."""
        set_current_tenant('tenant-b')
        orders = Order.objects.all()
        self.assertEqual(orders.count(), 3)
        for order in orders:
            self.assertEqual(order.tenant, 'tenant-b')

    # --- Negative tests (CRITICAL — prove isolation) ---

    def test_tenant_a_cannot_see_tenant_b_data(self):
        """Tenant A must NOT be able to see any of Tenant B's orders."""
        set_current_tenant('tenant-a')
        tenant_b_orders = Order.objects.filter(tenant='tenant-b')
        self.assertEqual(tenant_b_orders.count(), 0)

    def test_tenant_b_cannot_see_tenant_a_data(self):
        """Tenant B must NOT be able to see any of Tenant A's orders."""
        set_current_tenant('tenant-b')
        tenant_a_orders = Order.objects.filter(tenant='tenant-a')
        self.assertEqual(tenant_a_orders.count(), 0)

    def test_objects_all_cannot_bypass_scoping(self):
        """
        .objects.all() must be scoped — it should NEVER return all tenants' data.
        Even with .all(), results should only include current tenant's rows.
        """
        set_current_tenant('tenant-a')
        all_orders = Order.objects.all()
        # Should be 5 (tenant-a only), NOT 8 (all tenants)
        self.assertEqual(all_orders.count(), 5)

    def test_no_tenant_returns_empty_queryset(self):
        """
        If no tenant is set, TenantManager returns empty queryset (fail-closed).
        This prevents accidental data exposure when tenant context is missing.
        """
        clear_current_tenant()
        orders = Order.objects.all()
        self.assertEqual(orders.count(), 0)

    def test_unscoped_manager_returns_all(self):
        """The unscoped manager should bypass tenant filtering (for admin/migrations)."""
        orders = Order.unscoped.all()
        self.assertEqual(orders.count(), 8)

    # --- Middleware tests ---

    def test_middleware_sets_tenant_from_header(self):
        """Middleware should extract tenant from X-Tenant-ID header."""
        factory = RequestFactory()
        request = factory.get('/api/orders/slow/', HTTP_X_TENANT_ID='tenant-a')

        def mock_view(request):
            from django.http import JsonResponse
            # Inside the view, tenant should be set
            tenant = get_current_tenant()
            return JsonResponse({'tenant': tenant})

        middleware = TenantMiddleware(mock_view)
        response = middleware(request)

        import json
        data = json.loads(response.content)
        self.assertEqual(data['tenant'], 'tenant-a')

    def test_middleware_clears_tenant_after_request(self):
        """Tenant must be cleared after the request to prevent cross-request leaking."""
        factory = RequestFactory()
        request = factory.get('/api/orders/slow/', HTTP_X_TENANT_ID='tenant-a')

        def mock_view(request):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        middleware = TenantMiddleware(mock_view)
        middleware(request)

        # After the request, tenant should be cleared
        self.assertIsNone(get_current_tenant())

    def test_middleware_clears_tenant_even_on_exception(self):
        """Tenant must be cleared even if the view raises an exception."""
        factory = RequestFactory()
        request = factory.get('/api/orders/slow/', HTTP_X_TENANT_ID='tenant-a')

        def exploding_view(request):
            raise ValueError("Boom!")

        middleware = TenantMiddleware(exploding_view)

        with self.assertRaises(ValueError):
            middleware(request)

        # Tenant must still be cleared
        self.assertIsNone(get_current_tenant())
