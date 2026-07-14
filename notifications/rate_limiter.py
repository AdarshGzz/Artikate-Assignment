# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Rate limiter: sliding window log using Redis sorted sets + Lua script
# See DESIGN.md for algorithm justification and atomicity explanation
# ============================================

import time
import redis as redis_lib

# Lua script for atomic sliding window rate limiting.
# This runs as a single atomic operation in Redis — no race conditions
# possible because Redis executes Lua scripts on a single thread.
#
# Algorithm: Sliding Window Log
# - Uses a sorted set where each member is a unique request ID
#   and its score is the timestamp (in milliseconds) when it was made.
# - On each check:
#   1. Remove all entries outside the current window (ZREMRANGEBYSCORE)
#   2. Count remaining entries (ZCARD)
#   3. If under the limit, add the new entry (ZADD) and return 1 (allowed)
#   4. If at/over the limit, return 0 (denied)
# - Set a TTL on the key so it auto-cleans after the window expires.

SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]

-- Remove all entries outside the current window
local window_start = now - window_ms
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- Count remaining entries in the window
local current_count = redis.call('ZCARD', key)

if current_count < limit then
    -- Under the limit: add this request and allow it
    redis.call('ZADD', key, now, request_id)
    -- Set TTL to auto-cleanup (window size + small buffer)
    redis.call('PEXPIRE', key, window_ms + 1000)
    return 1
else
    -- At or over the limit: deny
    return 0
end
"""


class RateLimiter:
    """
    Sliding window rate limiter using Redis sorted sets.

    Uses a Lua script for atomicity — the entire check-and-increment
    operation runs as a single atomic command in Redis, preventing
    race conditions between concurrent workers.

    Fail-open design: if Redis is unavailable, requests are ALLOWED
    (email delivery is more important than rate limiting).
    """

    def __init__(self, redis_url='redis://localhost:6379/0'):
        self._redis = redis_lib.from_url(redis_url, decode_responses=True)
        self._script = self._redis.register_script(SLIDING_WINDOW_LUA)

    def is_allowed(self, key, limit, window_seconds, request_id=None):
        """
        Check if a request is allowed under the rate limit.

        Args:
            key: Rate limit key (e.g., 'email:rate_limit')
            limit: Maximum number of requests allowed in the window
            window_seconds: Size of the sliding window in seconds
            request_id: Unique identifier for this request (auto-generated if None)

        Returns:
            True if allowed, False if rate limited
        """
        if request_id is None:
            request_id = f"{time.time_ns()}"

        now_ms = int(time.time() * 1000)
        window_ms = int(window_seconds * 1000)

        try:
            result = self._script(
                keys=[key],
                args=[now_ms, window_ms, limit, request_id],
            )
            return bool(result)
        except (redis_lib.ConnectionError, redis_lib.TimeoutError):
            # Fail-open: if Redis is down, allow the request.
            # Rationale: delivering an email is more important than
            # perfect rate limiting. The external email provider has
            # its own rate limits as a backstop.
            return True

    def get_current_count(self, key, window_seconds):
        """Get the current number of requests in the sliding window (for monitoring)."""
        now_ms = int(time.time() * 1000)
        window_start = now_ms - int(window_seconds * 1000)
        try:
            # Clean up expired entries first
            self._redis.zremrangebyscore(key, '-inf', window_start)
            return self._redis.zcard(key)
        except (redis_lib.ConnectionError, redis_lib.TimeoutError):
            return 0
