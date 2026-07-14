# DESIGN.md — Section 2: Architecture Decisions

## Job Queue Selection

### Options Evaluated

| Criteria | Celery + Redis | Django-Q | Custom Queue |
|---|---|---|---|
| **Maturity** | Industry standard, 10+ years, massive community | Smaller community, fewer maintainers | No community — entirely on us |
| **Broker support** | Redis, RabbitMQ, SQS, and more | Redis or Django ORM | We'd build on top of Redis directly |
| **Retry/backoff** | Built-in `autoretry_for`, `retry_backoff`, `max_retries` | Basic retry support | Must implement from scratch |
| **Monitoring** | Flower, django-celery-results, rich logging | Django admin integration | Nothing — must build everything |
| **Learning curve** | Moderate — well-documented but has many knobs | Low — simpler API | Highest — debugging our own queue logic |
| **Production risk** | Low — battle-tested at scale | Medium — less proven at high throughput | High — untested in production |
| **Setup complexity** | Needs Redis + separate worker process | Simpler — can use ORM as broker | Redis only, but more custom code |

### Decision: Celery + Redis

**Why:** The assignment requires exponential backoff, dead-letter handling, and rate limiting — Celery gives us `max_retries`, `countdown`, and `bind=True` out of the box. Django-Q could work but has weaker retry semantics. A custom queue would mean reimplementing what Celery already provides, which is exactly the kind of over-engineering the brief tells us to avoid.

Redis as the broker is the simplest choice: it's already required for the rate limiter, so no additional infrastructure is needed. RabbitMQ would be more robust for guaranteed delivery, but Redis with `acks_late=True` is sufficient for this use case.

---

## Rate Limiter Design

### Algorithm: Sliding Window Log (Redis Sorted Set)

Three standard rate-limiting algorithms were evaluated:

| Algorithm | Accuracy | Memory | Atomicity | Complexity |
|---|---|---|---|---|
| **Fixed Window Counter** | Low — boundary burst problem (2x limit at window edges) | Very low (one counter) | Easy (INCR + EXPIRE) | Simplest |
| **Sliding Window Log** | Exact — every request tracked individually | Higher (one entry per request) | Atomic via Lua script | Moderate |
| **Token Bucket** | Good — smooth rate, allows controlled bursts | Low (counter + timestamp) | Needs Lua for refill + consume | Moderate |

### Why Sliding Window Log

1. **Exact accuracy:** With a 200/min limit and the test requiring "rate limit is never exceeded," we need precision. Fixed window allows up to 400 requests across a window boundary (200 at the end of window N, 200 at the start of window N+1, within a 60-second span). The sliding window log tracks every request timestamp and always counts exactly within the last 60 seconds.

2. **Simple mental model:** One sorted set, timestamps as scores. `ZREMRANGEBYSCORE` removes old entries, `ZCARD` counts current ones, `ZADD` adds new ones. Three Redis commands, easy to reason about.

3. **Memory is acceptable:** At 200/min, the sorted set holds at most 200 entries. Each entry is ~50 bytes (timestamp + request ID). That's ~10KB — negligible for Redis.

### How Atomicity Is Guaranteed

The entire check-and-increment operation is wrapped in a **Lua script** executed via `redis.call()`:

```lua
-- Runs as a single atomic operation on Redis's single-threaded event loop
local window_start = now - window_ms
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)  -- clean old
local current_count = redis.call('ZCARD', key)              -- count
if current_count < limit then
    redis.call('ZADD', key, now, request_id)                -- add
    return 1  -- allowed
end
return 0  -- denied
```

**Why this guarantees atomicity:** Redis executes Lua scripts on its single thread without interleaving other commands. Between the `ZCARD` check and the `ZADD` insert, no other client can modify the sorted set. This eliminates the race condition where two workers both read `count=199`, both decide they're under the limit, and both insert — resulting in 201 requests (exceeding the 200 limit).

An alternative is `MULTI`/`EXEC` (Redis transactions), but that doesn't support conditional logic — we can't do "check count, then conditionally add." `WATCH`/`MULTI`/`EXEC` supports optimistic locking but requires retry loops. Lua is simpler and more reliable.

### Fail-Open or Fail-Closed If Redis Goes Down

**Decision: Fail-open (allow requests).**

If Redis becomes unreachable, the rate limiter's `is_allowed()` method catches `ConnectionError` / `TimeoutError` and returns `True`.

**Rationale:**
- The primary goal is email delivery. If Redis is briefly down, we'd rather send emails (possibly slightly over the rate limit) than drop them entirely.
- The external email provider (SendGrid, SES, etc.) has its own server-side rate limits as a backstop — our rate limiter is a courtesy throttle, not a security boundary.
- A fail-closed approach would mean a Redis blip could halt all email notifications, which is worse than a temporary burst.

**Trade-off acknowledged:** If Redis is down for an extended period, we lose rate limiting entirely. In production, this would warrant an alert on Redis health + a circuit breaker pattern. But for this assessment, fail-open is the pragmatic choice.

---

## Celery Worker Configuration

Key settings for resilience:

| Setting | Value | Purpose |
|---|---|---|
| `acks_late` | `True` | Don't acknowledge the message until the task completes. If the worker dies mid-task, Redis still has the message. |
| `reject_on_worker_lost` | `True` | If the worker process is killed (SIGKILL), reject the message so the broker re-delivers it to another worker. |
| `max_retries` | `5` | Exponential backoff: 10s → 20s → 40s → 80s → 160s (total ~5 min of retries). |

See `ANSWERS.md` (Section 2) for detailed SIGKILL analysis.
