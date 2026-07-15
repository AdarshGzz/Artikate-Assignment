# Artikate Studio — Backend Assessment

Django backend assessment covering N+1 query diagnosis, rate-limited async job queue, multi-tenant data isolation, and written architecture review.

---

## ★ Live System Recording (Bonus)

A screen recording demonstrating the Celery queue implementation, Redis state, rate limiter throttling (limiting to <= 200 emails/minute), and retry mechanism with backoff:
- **[Live System Recording (Google Drive)](https://drive.google.com/file/d/1N6X3df12g8yVRHiTDwwGTED1ksdUGgAm/view)**

---

## Quick Start

### Prerequisites
- Python 3.10+
- Docker (for Redis) OR Redis installed locally

### Step 1 — Start Redis (pick one)

```bash
# Option A (if you have Docker — easiest, no install needed):
docker run -d -p 6379:6379 --name artikate-redis redis

# Option B (if Redis installed locally):
redis-server --daemonize yes
```

### Step 2 — Setup the project

```bash
make setup
```

This creates a virtual environment, installs dependencies, runs migrations, and seeds 500 orders for profiling.

### Step 3 — Run the server + Celery worker

```bash
make run
```

This starts the Celery worker in the background and the Django dev server at `http://127.0.0.1:8000/`.

### Step 4 — Run all tests

```bash
make test
```

Runs all 23 tests across both apps (orders + notifications).

### Step 5 — Stop background services

```bash
make stop
```

Kills the background Celery worker.

---

## Demonstrating the Queue & Rate Limiter (As Shown in the Video)

To run the queue and rate limiter in the foreground (useful for recording a screen demo or watching logs in real-time), open a terminal layout with multiple panes and run:

1. **Terminal Pane 1: Start Redis**
   ```bash
   # Option A (via Docker):
   docker run -it -p 6379:6379 --name artikate-redis redis
   
   # Option B (local redis-server):
   redis-server
   ```

2. **Terminal Pane 2: Start Django**
   ```bash
   source venv/bin/activate
   python manage.py runserver
   ```

3. **Terminal Pane 3: Start Celery worker in the foreground**
   ```bash
   source venv/bin/activate
   celery -A config worker -l info
   ```

4. **Terminal Pane 4: Monitor Redis in Real-Time (Optional)**
   To see rate limiter sorted set commands streaming:
   ```bash
   redis-cli monitor
   # Note: For Docker setups, run: docker exec -it artikate-redis redis-cli monitor
   ```

5. **Terminal Pane 5: Submit the Demo Jobs**
   Trigger the submission of 250 jobs (249 success, 1 failure to show backoff):
   ```bash
   source venv/bin/activate
   python submit_jobs.py
   ```

---

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/orders/slow/` | GET | Section 1: N+1 query view (unoptimized) |
| `/api/orders/fast/` | GET | Section 1: Optimized view (select_related + prefetch_related) |
| `/api/notifications/send/` | POST | Section 2: Queue an email notification |
| `/api/notifications/status/` | GET | Section 2: Check failed job count |
| `/__debug__/` | — | django-debug-toolbar (when DEBUG=True) |

### Testing tenant isolation (Section 3)

Pass the `X-Tenant-ID` header to scope queries:

```bash
# See only acme's orders
curl -H "X-Tenant-ID: acme" http://127.0.0.1:8000/api/orders/fast/

# See only globex's orders
curl -H "X-Tenant-ID: globex" http://127.0.0.1:8000/api/orders/fast/
```

---

## File Map

| Section | Files | Description |
|---|---|---|
| **Section 1** (N+1 Diagnosis) | `orders/models.py`, `orders/views.py` (`slow_order_list`, `fast_order_list`), `orders/management/commands/seed_data.py`, `orders/tests/test_n1_query.py` | Models, broken + fixed views, seed data, query count tests |
| **Section 2** (Job Queue) | `notifications/tasks.py`, `notifications/rate_limiter.py`, `notifications/models.py`, `notifications/views.py`, `notifications/tests/test_queue.py`, `config/celery.py` | Celery task, sliding window rate limiter, FailedJob model, queue tests |
| **Section 3** (Tenant Isolation) | `orders/managers.py`, `orders/middleware.py`, `orders/tenant.py`, `orders/tests/test_tenant_isolation.py` | TenantManager, middleware, thread-local storage, isolation tests |
| **Section 4** (Written) | `ANSWERS.md` | Django Admin optimization, file upload security |
| **Architecture** | `DESIGN.md` | Queue comparison, rate limiter design, atomicity, fail-open justification |

---

## Written Deliverables

- **[ANSWERS.md](ANSWERS.md)** — Incident investigation log (Section 1), SIGKILL analysis (Section 2), async thread-local failure mode (Section 3), Django Admin + file upload security (Section 4)
- **[DESIGN.md](DESIGN.md)** — Section 2 architecture decisions: queue comparison, rate limiter algorithm, atomicity guarantee, fail-open rationale

---

## Tech Stack

| Component | Choice | Reason |
|---|---|---|
| Framework | Django 4.2 LTS | Assignment requirement |
| Database | SQLite | Zero setup for reviewer |
| Task Queue | Celery 5.x | Built-in retry, backoff, acks_late |
| Broker | Redis | Also used for rate limiter |
| Profiler | django-debug-toolbar | Query count before/after evidence |
| Rate Limiter | Custom (Redis sorted set + Lua) | Assignment requires hand-built, no libraries |
