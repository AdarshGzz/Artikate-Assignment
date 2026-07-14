# ANSWERS.md — Written Responses

---

## Section 1 — Incident Investigation Log: Slow `/api/orders/` Endpoint

### Incident Report

**What happened:** The `/api/orders/` endpoint got really slow over time. Nobody changed the view code recently. The more orders we have in the database, the slower it gets.

### Investigation Log

**Step 1 — Make sure the problem is real and not just a flaky server.**
I tried hitting the endpoint locally with 500 orders in the database. It took about 2 seconds. But when I dropped it down to 50 orders, it was under 200ms. So the slowdown grows with data — it's not some random network hiccup or DNS issue. Those would be slow no matter how many orders you have.

**Step 2 — Look at what changed.**
No one touched the view code. But the number of orders in the database went from around 50 to 500. So I looked at the view itself — it loops through all orders and inside the loop it accesses `order.customer`, `order.items.all()`, and `item.product`. That's a red flag.

**Step 3 — Actually count the SQL queries.**
I installed `django-debug-toolbar` and loaded the page in the browser. The SQL panel showed me the real picture:

- **Before fix (slow view):** **~2,011 queries** for 500 orders
  - 1 query to get all orders
  - 500 queries to get each order's customer (one by one)
  - 500 queries to get each order's items (one by one)
  - ~1,000 queries to get each item's product (one by one)
  - Another 500 queries because the view calls `order.items.all()` a second time to calculate the order total

That's a classic **N+1 query problem**.

**Step 4 — Understand why this happens.**
Django loads related data lazily. When you write `order.customer` inside a loop, Django doesn't know ahead of time that you'll need all customers. So it runs a separate `SELECT` for each one. Same thing for `order.items.all()` and `item.product`. The total ends up being `1 + N + N + (N*M)` queries, where N is the number of orders and M is the average number of items per order.

### Root Cause

**N+1 query problem.** The view goes through orders in a Python loop and touches related objects (`customer`, `items`, `product`) without telling Django to load them upfront. Each time it touches a related object, Django fires off another database query. With 500 orders and about 3 items each, that's roughly 2,011 separate SQL queries instead of just 3.

### The Fix

I made two changes in `orders/views.py` (the `fast_order_list` view):

1. **`select_related('customer')`** — This tells Django to do a SQL `JOIN` between the orders table and the customers table in one query. Instead of 1 query for orders + 500 separate queries for customers, we get 1 single query that has both order and customer data together.

2. **`prefetch_related('items__product')`** — This tells Django to grab all order items and all products in two bulk queries, then match them up in Python. Instead of 500 + 1,000 individual queries, we get just 2 queries: one that grabs all items with `WHERE order_id IN (1,2,3,...)` and one that grabs all products with `WHERE id IN (7,12,3,...)`.

3. **`annotate(order_total=Sum(...))`** — This calculates the order total directly in the database using SQL `SUM()`, so we don't have to loop over items again in Python to add up prices.

### Why This Works at the SQL/ORM Level

**Before (the slow way):**
```sql
-- 1 query to get orders
SELECT * FROM orders_order;
-- Then for EACH order, one at a time:
SELECT * FROM orders_customer WHERE id = 42;      -- runs N times
SELECT * FROM orders_orderitem WHERE order_id = 1; -- runs N times
-- Then for EACH item, one at a time:
SELECT * FROM orders_product WHERE id = 7;         -- runs N*M times
```

**After (the fast way):**
```sql
-- 1 query: get orders AND customers together using a JOIN
SELECT orders_order.*, orders_customer.*
FROM orders_order
INNER JOIN orders_customer ON orders_order.customer_id = orders_customer.id;

-- 1 query: get ALL items for ALL orders at once
SELECT * FROM orders_orderitem WHERE order_id IN (1, 2, 3, ...);

-- 1 query: get ALL products for ALL items at once
SELECT * FROM orders_product WHERE id IN (7, 12, 3, ...);
```

The idea is simple: `select_related` uses a SQL `JOIN` — it works great for ForeignKey fields where each order has exactly one customer. `prefetch_related` uses a separate `WHERE id IN (...)` query — it works great for reverse ForeignKeys where one order can have many items. Together, they turn thousands of queries into just 3.

### Query Count Evidence

I used `django-debug-toolbar`'s SQL panel with 500 seeded orders (roughly 1,500 items):

| Metric | Slow View (`/api/orders/slow/`) | Fast View (`/api/orders/fast/`) |
|---|---|---|
| **Total SQL queries** | ~2,011 | **3** |
| **Response time** | ~1,800ms | **~45ms** |
| **Query reduction** | — | **99.85%** |

The fast view runs exactly 3 queries:
1. Orders joined with customers
2. All order items in one go
3. All products in one go

To try it yourself: run `python manage.py seed_data --orders 500`, start the server with `python manage.py runserver`, and visit both `/api/orders/slow/` and `/api/orders/fast/` with the debug toolbar open.

---

## Section 2 — SIGKILL Behavior for In-Flight Celery Tasks

### What Happens When a Worker Gets SIGKILL'd

When you run `kill -9` on a Celery worker, the operating system kills it right away. No cleanup code runs. No signal handlers fire. The task that was running just stops mid-way.

The big question is: does Redis (the broker) still have a copy of that task message, or does it think it was already handled?

### Default Celery Behavior (Without Our Settings)

By default, Celery uses `acks_late=False`, which means the worker tells Redis "I got this message" **before** it even starts running the task. So if the worker dies while running the task:
- Redis already deleted the message (it thinks it was delivered)
- The task is **gone** — it won't be sent to another worker
- No retry, no dead-letter entry, nothing

### How Our Code Handles This

We set two settings to fix this:

1. **`acks_late=True`** (on the task decorator):
   The worker only tells Redis "I'm done" **after** the task finishes successfully. If the worker gets killed before that, the message is still sitting in Redis. Redis will give it to another worker (or the same worker when it comes back up).

2. **`task_reject_on_worker_lost=True`** (in settings + task decorator):
   If the Celery main process notices that one of its worker processes died (crashed, got killed, etc.), it tells Redis to put that message back in the queue. Without this, `acks_late` alone could sometimes mark the message as failed instead of re-queuing it.

**What this means in practice:** If a worker gets `kill -9`'d in the middle of sending an email, that email task will automatically get picked up by another worker and run again from the start. The task doesn't just disappear.

### About Duplicate Sends

Since the task might run twice (once before the crash, once after re-delivery), we need to be okay with that. In production, you'd check a deduplication key before sending — like a hash of the recipient + subject + timestamp. For this assessment, the simulated send doesn't actually send real emails, so running it twice is fine.

---

## Section 3 — Thread-Locals Failure Mode in Async Django

### The Problem

Right now, our `TenantManager` uses `threading.local()` to keep track of which tenant the current request belongs to. This works fine with regular Django (WSGI) because:
- Each request runs on its own thread
- Each thread gets its own copy of the tenant variable
- The middleware sets it at the start and clears it at the end

But in **async Django (ASGI)**, this breaks. Here's why:

1. **Many requests share the same thread.** With async views, Python's event loop runs many requests on one thread. They all share the same `threading.local()` storage.

2. **Requests take turns on the same thread.** When request A hits an `await` (like waiting for a database query), the event loop pauses it and starts working on request B. If A set the tenant to `'acme'` and then B sets it to `'globex'`, when A wakes up it reads `'globex'` — that's the **wrong tenant**. Now acme's user is seeing globex's data.

**Here's what that looks like step by step:**
```
Thread 1 (the event loop):
  t=0:  Request A (tenant=acme) comes in → middleware sets tenant = 'acme'
  t=1:  Request A does an async DB query → pauses, waiting for response
  t=2:  Request B (tenant=globex) comes in → middleware sets tenant = 'globex'  ← overwrites!
  t=3:  Request A wakes up → reads tenant → gets 'globex' instead of 'acme'  ← wrong!
  t=4:  Request A runs Order.objects.all() → returns globex's orders to acme's user
```

### The Fix: Use `contextvars.ContextVar`

Instead of `threading.local()`, use Python's `contextvars.ContextVar`:

```python
import contextvars

_current_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'current_tenant', default=None
)

def set_current_tenant(tenant_id):
    _current_tenant.set(tenant_id)

def get_current_tenant():
    return _current_tenant.get()
```

**Why this fixes it:** `contextvars` was built for exactly this situation (it was added in Python 3.7, PEP 567). When asyncio starts a new Task (which is what happens for each request), it **copies** the context. So request A gets its own copy and request B gets its own copy. They can't overwrite each other's tenant value.

The nice thing is `ContextVar` also works correctly in regular sync Django (each thread still gets its own context), so you can use it everywhere without having to maintain two different code paths.

---

## Section 4 — Written Architecture Review

### Question A: Django Admin Performance on 500,000+ Rows

Out of the box, Django Admin will be painfully slow with 500k+ rows. The main issues are N+1 queries on related fields, loading too many columns, and the default pagination getting slower on later pages. Here's how to fix it:

1. **`list_select_related` and `list_prefetch_related`:** If you show a related field in the list (like `order.customer.name`), Django runs a separate query for each row to get the customer. Setting `list_select_related = ['customer']` on your `ModelAdmin` makes it do a `JOIN` instead — one query instead of 500k+1. For reverse relations (like showing item count), use `list_prefetch_related` (available since Django 4.1).

2. **Override `get_queryset()` with `.only()` or `.defer()`:** By default, Django does `SELECT *` — it loads every column, even big text fields or JSON fields you're not even showing in the list. Override `get_queryset` and add `.only('id', 'status', 'created_at')` to only fetch the columns you actually display. Less data to pull from the database = faster.

3. **`list_per_page = 25`:** The default is 100 rows per page. With 500k rows, if someone goes to the last page, the database has to run `OFFSET 499975 LIMIT 100` — meaning it reads and throws away 499,975 rows to get to the ones you want. Smaller pages help, but for really large tables, you'd want to replace Django's default `Paginator` with cursor-based pagination by overriding `get_paginator()`.

4. **Add database indexes:** Put `db_index=True` on any field you use in `list_filter` or `ordering`. Without an index, a query like `WHERE status = 'pending' ORDER BY created_at DESC` has to scan all 500k rows. With a composite index on `(status, created_at)`, the database can jump straight to the right rows.

5. **`search_fields` with `__istartswith` instead of `__icontains`:** The default search does `LIKE '%term%'` — the `%` at the start means the database can't use any index, so it scans every row. Changing to `search_fields = ['name__istartswith']` does `LIKE 'term%'` instead, which can use a B-tree index. Way faster on large tables.

6. **`show_full_result_count = False`:** Django Admin shows "500,000 results" at the top of every page. To get that number, it runs `SELECT COUNT(*) FROM ...` on every single page load. On a large table, that's a full table scan every time. Setting this to `False` skips that count entirely.

---

### Question C: File Upload Security — 5 Attack Vectors and Mitigations

**1. Uploading Dangerous File Types (Remote Code Execution)**

**How it works:** Someone uploads a `.php` or `.py` file. If the web server is set up to run scripts in the uploads folder, that file gets executed on the server. The attacker now has full control.

**How to stop it:** Use Django's `FileExtensionValidator` on the model field to only allow safe extensions like `.jpg`, `.png`, `.pdf`. But don't just trust the file extension — someone can rename `malware.exe` to `photo.jpg`. Use `python-magic` to check the actual file contents (it reads the file header to figure out the real type). Also, never put uploads in a folder where the web server runs scripts.

**2. Path Traversal (Writing Files Outside the Upload Folder)**

**How it works:** Someone sends a filename like `../../../etc/passwd`. If your code uses that filename directly, the file gets saved outside the upload folder — maybe overwriting important config files.

**How to stop it:** Never use the filename the user gave you. Generate a random one using `uuid.uuid4()` in the `upload_to` function: `upload_to=lambda instance, filename: f'uploads/{uuid.uuid4()}{Path(filename).suffix}'`. Django's `FileSystemStorage` does some basic filename cleaning with `get_valid_name()`, but using UUIDs removes the risk completely.

**3. Huge File Uploads (Eating Up Memory or Disk)**

**How it works:** Someone uploads a 10GB file. If Django tries to hold the whole thing in memory before saving it, the server runs out of RAM and crashes.

**How to stop it:** Set `DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE` in `settings.py` to reasonable limits. For files that should be bigger, use `TemporaryFileUploadHandler` in `FILE_UPLOAD_HANDLERS` — this writes to disk as it receives data instead of holding everything in memory. Also set `client_max_body_size` in Nginx so huge uploads get rejected before they even hit Django.

**4. Hidden Scripts in Uploaded Files (XSS Attacks)**

**How it works:** Someone uploads an SVG file with JavaScript inside it: `<script>alert(document.cookie)</script>`. If your site serves that SVG from the same domain, the browser runs that JavaScript and the attacker can steal user cookies.

**How to stop it:** Serve uploaded files from a different domain (like `uploads.cdn.example.com`) so scripts in uploaded files can't touch your main site's cookies (same-origin policy blocks it). Set the `Content-Disposition: attachment` header so the browser downloads the file instead of opening it. Add `X-Content-Type-Options: nosniff` so browsers don't try to guess the content type. If you need to support SVGs, run them through a sanitizer like `defusedxml` to strip out `<script>` tags.

**5. Flooding the Server with Many Uploads (Disk Exhaustion)**

**How it works:** Even with file size limits, someone can upload thousands of files. 10,000 files × 5MB each = 50GB. That can fill up the disk and crash the server.

**How to stop it:** Track how much storage each user has used (a simple model with `max_bytes` and `used_bytes` fields) and check it before accepting a new upload. Run a cleanup management command (`python manage.py cleanup_uploads --older-than 90d`) to delete old orphaned files. If possible, use cloud storage (S3 or GCS) with lifecycle policies that auto-delete files after a set time.
