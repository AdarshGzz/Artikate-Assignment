# ============================================
# SECTION 1 — N+1 Query Diagnosis
# Models: Customer, Product, Order, OrderItem
# See ANSWERS.md for investigation log
# ============================================
# SECTION 3 — Multi-Tenant Data Isolation
# Order model uses TenantManager for auto-scoping
# See ANSWERS.md for async thread-local explanation
# ============================================

from django.db import models
from .managers import TenantManager


class Customer(models.Model):
    """A customer who places orders."""
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Product(models.Model):
    """A product that can be ordered."""
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Order(models.Model):
    """
    An order placed by a customer.
    Uses TenantManager as default manager for automatic tenant scoping (Section 3).
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='orders',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    tenant = models.CharField(max_length=100, db_index=True, default='default')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Section 3: TenantManager auto-filters by current tenant
    objects = TenantManager()
    # Unscoped manager for admin and migrations
    unscoped = models.Manager()

    def __str__(self):
        return f"Order #{self.pk} — {self.customer.name}"

    class Meta:
        ordering = ['-created_at']
        # Use unscoped manager as the default for admin/migrations
        default_manager_name = 'unscoped'


class OrderItem(models.Model):
    """A line item within an order."""
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='order_items',
    )
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    @property
    def line_total(self):
        return self.quantity * self.product.price

    class Meta:
        ordering = ['id']
