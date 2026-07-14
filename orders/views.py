# ============================================
# SECTION 1 — N+1 Query Diagnosis
# Views: slow_view (N+1 problem) and fast_view (optimized)
# See ANSWERS.md for investigation log and query counts
# ============================================

from django.http import JsonResponse
from django.db.models import Sum, F, DecimalField
from .models import Order, OrderItem


def slow_order_list(request):
    """
    BROKEN VIEW — Demonstrates the N+1 query problem.

    This view iterates over all orders and accesses related objects
    (customer, items, product) without prefetching. For N orders with
    M items each, this generates:
        1 query for orders
      + N queries for customer (one per order)
      + N queries for items (one per order)
      + M queries for product (one per item)
      = 1 + N + N + (N*M) queries total

    With 500 orders and ~3 items each, this is ~2500 queries.
    """
    orders = Order.unscoped.all()

    result = []
    for order in orders:
        # N+1 #1: accessing order.customer triggers a separate query per order
        customer_name = order.customer.name
        customer_email = order.customer.email

        items = []
        # N+1 #2: accessing order.items triggers a separate query per order
        for item in order.items.all():
            # N+1 #3: accessing item.product triggers a separate query per item
            items.append({
                'product_name': item.product.name,
                'product_price': str(item.product.price),
                'quantity': item.quantity,
                'line_total': str(item.quantity * item.product.price),
            })

        result.append({
            'id': order.id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'items': items,
            'order_total': str(sum(
                item.quantity * item.product.price
                for item in order.items.all()  # N+1 #4: ANOTHER query for items!
            )),
        })

    return JsonResponse({'orders': result, 'count': len(result)})


def fast_order_list(request):
    """
    FIXED VIEW — Eliminates N+1 queries using select_related and prefetch_related.

    Optimization:
    1. select_related('customer') — performs a SQL JOIN to fetch customer
       data in the same query as orders (turns N+1 → 1 query for customers).
    2. prefetch_related('items__product') — fetches all OrderItems and their
       related Products in just 2 additional queries (one for items, one for
       products), regardless of how many orders exist.

    Total queries: 3 (orders+customers JOIN, items, products)
    vs. the slow view's 1 + N + N + (N*M) queries.

    Additionally uses annotate() to compute order totals at the DB level
    instead of in Python loops.
    """
    orders = (
        Order.unscoped.all()
        .select_related('customer')
        .prefetch_related('items__product')
        .annotate(
            order_total=Sum(
                F('items__quantity') * F('items__product__price'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
    )

    result = []
    for order in orders:
        # No extra queries — customer is already loaded via JOIN
        customer_name = order.customer.name
        customer_email = order.customer.email

        items = []
        # No extra queries — items and products are prefetched
        for item in order.items.all():
            items.append({
                'product_name': item.product.name,
                'product_price': str(item.product.price),
                'quantity': item.quantity,
                'line_total': str(item.quantity * item.product.price),
            })

        result.append({
            'id': order.id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'items': items,
            # order_total computed at DB level via annotate()
            'order_total': str(order.order_total or 0),
        })

    return JsonResponse({'orders': result, 'count': len(result)})
