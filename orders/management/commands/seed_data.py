# ============================================
# SECTION 1 — N+1 Query Diagnosis
# Management command to seed test data for profiling
# ============================================

import random
from django.core.management.base import BaseCommand
from orders.models import Customer, Product, Order, OrderItem


class Command(BaseCommand):
    help = 'Seed the database with sample data for N+1 query profiling (Section 1)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--orders',
            type=int,
            default=500,
            help='Number of orders to create (default: 500)',
        )

    def handle(self, *args, **options):
        num_orders = options['orders']

        self.stdout.write('Seeding database...')

        # Create customers
        customers = []
        for i in range(50):
            customer, _ = Customer.objects.get_or_create(
                email=f'customer{i}@example.com',
                defaults={'name': f'Customer {i}'},
            )
            customers.append(customer)
        self.stdout.write(f'  Created {len(customers)} customers')

        # Create products
        products = []
        for i in range(30):
            product, _ = Product.objects.get_or_create(
                name=f'Product {i}',
                defaults={'price': round(random.uniform(9.99, 299.99), 2)},
            )
            products.append(product)
        self.stdout.write(f'  Created {len(products)} products')

        # Create orders with items
        statuses = ['pending', 'processing', 'shipped', 'delivered', 'cancelled']
        tenants = ['acme', 'globex', 'initech']

        orders_created = 0
        items_created = 0

        for i in range(num_orders):
            order = Order.unscoped.create(
                customer=random.choice(customers),
                status=random.choice(statuses),
                tenant=random.choice(tenants),
            )
            orders_created += 1

            # Each order gets 1–5 items
            num_items = random.randint(1, 5)
            for _ in range(num_items):
                OrderItem.objects.create(
                    order=order,
                    product=random.choice(products),
                    quantity=random.randint(1, 10),
                )
                items_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done! Created {orders_created} orders with {items_created} items total.'
        ))
