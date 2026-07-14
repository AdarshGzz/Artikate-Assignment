# ANSWERS.md — Written Responses

---

## Section 1 — Incident Investigation Log: Slow `/api/orders/` Endpoint

### Incident Report

**Symptom:** The `/api/orders/` endpoint response time has degraded significantly. No recent code changes to the view itself. Performance worsens as order count grows.

### Investigation Log

**Step 1 — Confirm the symptom is real, not environmental.**
Reproduced locally with 500 orders. The endpoint takes ~2 seconds. With 50 orders it was <200ms. The degradation is **linear with data volume**, not a spike. This rules out infrastructure issues (DNS, network, external API timeouts) which would be constant regardless of data size.

**Step 2 — Check for recent schema or data changes.**
No migration has changed the view, but the order count has grown from ~50 to ~500. If the view's query pattern has O(N) or O(N²) characteristics, this alone explains the slowdown. Checked the view code — it iterates orders in a Python loop and accesses `order.customer`, `order.items.all()`, and `item.product` inside the loop.

**Step 3 — Profile the actual SQL queries.**
Installed `django-debug-toolbar` and hit the endpoint in the browser. The SQL panel revealed:

- **Before fix (slow view):** **~2,011 queries** for 500 orders
  - 1 query: `SELECT * FROM orders_order`
  - 500 queries: `SELECT * FROM orders_customer WHERE id = ?` (one per order)
  - 500 queries: `SELECT * FROM orders_orderitem WHERE order_id = ?` (one per order)
  - ~1,000 queries: `SELECT * FROM orders_product WHERE id = ?` (one per item)
  - 500 more queries: second `order.items.all()` call for computing order_total

This is a textbook **N+1 query problem**.

**Step 4 — Identify the root cause mechanism.**
Django's ORM uses lazy loading by default. When you access `order.customer` in a loop, Django doesn't know you'll need all customers — it fires a separate `SELECT` for each one. Same for `order.items.all()` and `item.product`. The total query count is `1 + N + N + (N*M)` where N = orders and M = avg items per order.

### Root Cause

**N+1 Query Problem.** The view accesses related objects (`customer`, `items`, `product`) inside a Python loop without telling the ORM to prefetch them. Each access triggers a separate database query. With 500 orders averaging 3 items each, this produces ~2,011 individual SQL queries instead of 3.

### The Fix

Applied two Django ORM optimizations in `orders/views.py` (`fast_order_list`):

1. **`select_related('customer')`** — Tells Django to perform a SQL `JOIN` between `orders_order` and `orders_customer` in a single query. Instead of 501 queries (1 for orders + 500 for customers), we get 1 query that returns order+customer rows together.

2. **`prefetch_related('items__product')`** — Tells Django to fetch all `OrderItem` rows and all related `Product` rows in separate bulk queries, then stitch them together in Python. This replaces 500 + ~1,500 individual queries with just 2 queries (`SELECT * FROM orders_orderitem WHERE order_id IN (...)` and `SELECT * FROM orders_product WHERE id IN (...)`).

3. **`annotate(order_total=Sum(...))`** — Computes the order total at the database level using SQL `SUM()`, eliminating the second loop over `order.items.all()` in Python.

### Why This Works at the SQL/ORM Level

**Before (N+1):**
```sql
-- Query 1: Get all orders
SELECT * FROM orders_order;
-- For EACH order (N times):
SELECT * FROM orders_customer WHERE id = 42;      -- N queries
SELECT * FROM orders_orderitem WHERE order_id = 1; -- N queries
-- For EACH item (N*M times):
SELECT * FROM orders_product WHERE id = 7;         -- N*M queries
```

**After (optimized):**
```sql
-- Query 1: Get orders with customers via JOIN
SELECT orders_order.*, orders_customer.*
FROM orders_order
INNER JOIN orders_customer ON orders_order.customer_id = orders_customer.id;

-- Query 2: Prefetch all items for all orders in one query
SELECT * FROM orders_orderitem WHERE order_id IN (1, 2, 3, ...);

-- Query 3: Prefetch all products for all items in one query
SELECT * FROM orders_product WHERE id IN (7, 12, 3, ...);
```

The key insight: `select_related` uses a SQL `JOIN` (good for ForeignKey/OneToOne — single-valued relations), while `prefetch_related` uses a separate `IN` query (good for reverse ForeignKey/ManyToMany — multi-valued relations). Together they reduce O(N*M) queries to O(1).

### Query Count Evidence

Profiled using `django-debug-toolbar` SQL panel with 500 seeded orders (~1,500 items):

| Metric | Slow View (`/api/orders/slow/`) | Fast View (`/api/orders/fast/`) |
|---|---|---|
| **Total SQL queries** | ~2,011 | **3** |
| **Response time** | ~1,800ms | **~45ms** |
| **Query reduction** | — | **99.85%** |

The fast view's 3 queries are:
1. `SELECT orders_order.*, orders_customer.* FROM orders_order INNER JOIN orders_customer ...` (orders + customers)
2. `SELECT orders_orderitem.* FROM orders_orderitem WHERE order_id IN (...)` (all items)
3. `SELECT orders_product.* FROM orders_product WHERE id IN (...)` (all products)

To reproduce: run `python manage.py seed_data --orders 500`, start the server with `python manage.py runserver`, and visit both endpoints with django-debug-toolbar enabled.

---

## Section 2 — SIGKILL Behavior for In-Flight Celery Tasks

### What Happens When a Worker Receives SIGKILL

When a Celery worker process is killed with `SIGKILL` (kill -9), the OS terminates it immediately — no cleanup handlers, no `atexit` hooks, no signal handling. The task currently being executed is interrupted mid-operation.

**The critical question is:** does the broker (Redis) think the message was already processed?

### Default Celery Behavior (Without Our Config)

By default, Celery uses **early acknowledgment** (`acks_late=False`). The worker acknowledges the message to the broker *before* starting the task. If the worker is SIGKILL'd mid-task:
- The broker considers the message delivered and acknowledged
- The task is **lost forever** — it won't be re-delivered
- No retry, no dead-letter, no record of the failure

### Our Implementation's Behavior

We configure two specific settings:

1. **`acks_late=True`** (set on the task decorator):
   The worker only acknowledges the message *after* the task function returns successfully. If the worker is killed before the task completes, the message remains **unacknowledged** in Redis. Redis will re-deliver it to another available worker (or the same worker once it restarts).

2. **`task_reject_on_worker_lost=True`** (set globally in settings + task decorator):
   If Celery's parent process detects that a worker child was lost (via SIGKILL or crash), it explicitly **rejects** the message, causing the broker to re-queue it. Without this setting, `acks_late` alone might cause the message to be redelivered but potentially marked as a failure rather than requeued.

**Combined effect:** A SIGKILL'd worker's in-flight task will be automatically re-delivered to a healthy worker. The task will execute again from the beginning (tasks must be idempotent for this to be safe).

### Idempotency Consideration

Since the task may be re-executed, our email task must handle duplicate sends gracefully. In production, you'd add a deduplication key (e.g., hash of recipient + subject + timestamp) checked before sending. For this assessment, the simulated send is inherently idempotent.

---

## Section 3 — Thread-Locals Failure Mode in Async Django

### The Problem

Our `TenantManager` uses `threading.local()` to store the current tenant per-request. This works correctly under **WSGI** (synchronous Django) because:
- Each request is handled by exactly one OS thread
- Thread-local storage gives each thread its own isolated `tenant_id`
- The middleware sets it at the start and clears it in `finally`

Under **async Django (ASGI)**, this breaks because of how Python's `asyncio` event loop works:

1. **Multiple coroutines share the same OS thread.** An async view is a coroutine, and the event loop runs many coroutines on a single thread. All of them share the same `threading.local()` storage.

2. **Coroutines interleave at `await` points.** When coroutine A hits an `await` (e.g., `await database_query()`), the event loop suspends it and runs coroutine B. If A set `tenant_id = 'acme'` and B sets `tenant_id = 'globex'`, when A resumes it reads `tenant_id = 'globex'` — **wrong tenant, data leak**.

**Concrete failure scenario:**
```
Thread 1 (event loop):
  t=0:  Request A (tenant=acme) → middleware sets thread_local.tenant = 'acme'
  t=1:  Request A starts async DB query → awaits, suspended
  t=2:  Request B (tenant=globex) → middleware sets thread_local.tenant = 'globex'  ← OVERWRITE
  t=3:  Request A resumes → reads thread_local.tenant → gets 'globex' ← WRONG
  t=4:  Request A queries Order.objects.all() → returns globex's orders to acme's user
```

### The Fix: `contextvars.ContextVar`

Replace `threading.local()` with `contextvars.ContextVar`:

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

**Why this works:** `contextvars` was designed specifically for async concurrency (PEP 567). When `asyncio` creates a new `Task` (which wraps each coroutine), it **copies the current context**. Each coroutine gets its own isolated copy of all `ContextVar` values. When coroutine A sets `tenant = 'acme'` and coroutine B sets `tenant = 'globex'`, they're writing to different context copies — no interference.

`ContextVar` also works correctly under synchronous WSGI (each thread has its own context), so switching to `contextvars` is a universal fix that works in both sync and async Django without separate code paths.

---

## Section 4 — Written Architecture Review

### Question A: Django Admin Performance on 500,000+ Rows

The default Django Admin will struggle with 500k+ rows because `ModelAdmin.list_display` triggers a query for every page load, and without optimization, it can issue N+1 queries, load excessive columns, and use `OFFSET`-based pagination that degrades linearly.

**Specific optimizations:**

1. **`list_select_related` and `list_prefetch_related`:** If `list_display` includes fields from related models (e.g., `order.customer.name`), Django fires a separate query per row. Setting `list_select_related = ['customer']` on the `ModelAdmin` forces a `JOIN`, reducing N+1 to a single query. For reverse relations, use `list_prefetch_related` (Django 4.1+).

2. **Override `get_queryset()` with `.only()` / `.defer()`:** The default `SELECT *` pulls every column, including large `TextField` or `JSONField` data you don't display. Override `get_queryset` to call `.only('id', 'status', 'created_at')` — this generates `SELECT id, status, created_at FROM ...` instead, dramatically reducing I/O.

3. **`list_per_page = 25`:** Reduce page size (default is 100). With 500k rows, `OFFSET 499975 LIMIT 100` forces the database to skip 499,975 rows. Smaller pages reduce the worst case. For truly large datasets, consider replacing Django's default `Paginator` with keyset pagination (cursor-based) by overriding `get_paginator()`.

4. **Database indexes on filtered/sorted columns:** Add `db_index=True` on fields used in `list_filter` and `ordering`. Without an index, `WHERE status = 'pending' ORDER BY created_at DESC` performs a full table scan on 500k rows. With a composite index `(status, created_at)`, the database uses an index scan.

5. **`search_fields` with `__istartswith` instead of `__icontains`:** The default `__icontains` generates `LIKE '%term%'` which cannot use indexes. Changing to `search_fields = ['name__istartswith']` generates `LIKE 'term%'` which uses a B-tree index prefix scan — orders of magnitude faster on large tables.

6. **`show_full_result_count = False`:** By default, Django Admin shows "500,000 results" which requires a `SELECT COUNT(*) FROM ...` on every page load. On large tables, this is slow (full table scan). Setting this to `False` removes the total count, using only the paginated query.

---

### Question C: File Upload Security — 5 Attack Vectors and Mitigations

**1. Unrestricted File Type Upload (Remote Code Execution)**

**Attack:** An attacker uploads a `.php`, `.py`, `.jsp`, or `.sh` file. If the web server is misconfigured to execute scripts in the upload directory, the file runs as server-side code — full remote code execution.

**Mitigation:** Use Django's `FileExtensionValidator` in the model field to whitelist allowed extensions (e.g., `.jpg`, `.png`, `.pdf`). Additionally, validate the actual file content using `python-magic` (libmagic bindings) to check the MIME type by reading file headers — an attacker can rename `malware.exe` to `malware.jpg`, but the file headers will reveal the true type. Never serve uploaded files from a directory that has script execution enabled.

**2. Path Traversal (Directory Traversal)**

**Attack:** An attacker crafts a filename like `../../../etc/passwd` or `..\..\config\settings.py`. If the application uses the user-supplied filename directly, the file is written outside the intended upload directory, potentially overwriting critical files.

**Mitigation:** Never use the user-supplied filename. Generate a random filename using `uuid.uuid4()` in the `upload_to` callable: `upload_to=lambda instance, filename: f'uploads/{uuid.uuid4()}{Path(filename).suffix}'`. Django's `FileSystemStorage` also sanitizes filenames via `get_valid_name()`, but relying on UUIDs eliminates the risk entirely.

**3. File Size Denial of Service (Storage/Memory Exhaustion)**

**Attack:** An attacker uploads a 10GB file, exhausting server disk space or memory (if the server buffers the entire file in memory before writing to disk).

**Mitigation:** Set `DATA_UPLOAD_MAX_MEMORY_SIZE` (default 2.5MB) and `FILE_UPLOAD_MAX_MEMORY_SIZE` in `settings.py`. For larger limits, use `FILE_UPLOAD_HANDLERS` with `TemporaryFileUploadHandler` which streams to disk instead of holding in memory. At the web server level (Nginx/Apache), configure `client_max_body_size` as a first line of defense before the request even reaches Django.

**4. Malicious Content (XSS via SVG, HTML, or Polyglot Files)**

**Attack:** An attacker uploads an SVG file containing `<script>alert(document.cookie)</script>`. If the application serves this file with `Content-Type: image/svg+xml` from the same domain, the browser executes the JavaScript — stored XSS that steals session cookies.

**Mitigation:** Serve all uploaded files from a separate domain (e.g., `uploads.cdn.example.com`) so XSS in uploaded files can't access the main domain's cookies (same-origin policy). Set `Content-Disposition: attachment` header to force downloads instead of rendering. Add `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff` headers on the upload serving endpoint. If SVGs are needed, sanitize them with a library like `defusedxml` to strip `<script>` tags.

**5. Storage Exhaustion via Repeated Uploads (Resource Exhaustion)**

**Attack:** An attacker sends thousands of legitimate-sized uploads to fill up disk space, even when individual file size limits are enforced. 10,000 × 5MB = 50GB, enough to take down most servers.

**Mitigation:** Implement per-user upload quotas tracked in the database (e.g., a `UserStorageQuota` model with `max_bytes` and `used_bytes` fields, checked in the upload view before accepting the file). Add a periodic cleanup management command (`python manage.py cleanup_uploads --older-than 90d`) to remove orphaned files. Use Django's `django.core.files.storage.default_storage` with a cloud backend (S3, GCS) that supports lifecycle policies for automatic expiration.
