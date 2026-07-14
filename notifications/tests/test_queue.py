# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Tests: 500-job submission, rate limit enforcement, retry verification
# See DESIGN.md for architecture decisions
# ============================================

import time
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from notifications.models import FailedJob
from notifications.rate_limiter import RateLimiter


class RateLimiterTest(TestCase):
    """Tests for the sliding window rate limiter (Redis sorted set + Lua script)."""

    def setUp(self):
        """Set up a real Redis connection for rate limiter tests."""
        try:
            self.limiter = RateLimiter(redis_url='redis://localhost:6379/1')
            # Use a test-specific key prefix to avoid collisions
            self.test_key = f'test:rate_limit:{time.time_ns()}'
            # Verify Redis is actually available
            self.limiter._redis.ping()
            self.redis_available = True
        except Exception:
            self.redis_available = False

    def tearDown(self):
        """Clean up test keys."""
        if self.redis_available:
            self.limiter._redis.delete(self.test_key)

    def test_allows_requests_under_limit(self):
        """Requests under the rate limit should be allowed."""
        if not self.redis_available:
            self.skipTest('Redis not available')

        for i in range(10):
            result = self.limiter.is_allowed(
                key=self.test_key, limit=20, window_seconds=60,
                request_id=f'req-{i}',
            )
            self.assertTrue(result, f"Request {i} should be allowed (under limit of 20)")

    def test_blocks_requests_over_limit(self):
        """Requests over the rate limit should be blocked."""
        if not self.redis_available:
            self.skipTest('Redis not available')

        # Fill up the limit
        for i in range(10):
            self.limiter.is_allowed(
                key=self.test_key, limit=10, window_seconds=60,
                request_id=f'fill-{i}',
            )

        # The 11th request should be blocked
        result = self.limiter.is_allowed(
            key=self.test_key, limit=10, window_seconds=60,
            request_id='overflow',
        )
        self.assertFalse(result, "Request over the limit should be blocked")

    def test_rate_limit_200_per_minute_not_exceeded(self):
        """
        Submit more than 200 requests — assert that exactly 200 are allowed
        and the rest are denied. This proves the rate limiter enforces 200/min.
        """
        if not self.redis_available:
            self.skipTest('Redis not available')

        allowed = 0
        denied = 0
        total = 250

        for i in range(total):
            result = self.limiter.is_allowed(
                key=self.test_key, limit=200, window_seconds=60,
                request_id=f'batch-{i}',
            )
            if result:
                allowed += 1
            else:
                denied += 1

        self.assertEqual(allowed, 200, "Exactly 200 requests should be allowed")
        self.assertEqual(denied, 50, "50 requests should be denied")

    def test_fail_open_when_redis_unavailable(self):
        """If Redis is down, the rate limiter should fail-open (allow requests)."""
        limiter = RateLimiter(redis_url='redis://localhost:9999/0')  # wrong port
        result = limiter.is_allowed(
            key='test:fail_open', limit=1, window_seconds=60,
            request_id='test',
        )
        self.assertTrue(result, "Should fail-open when Redis is unavailable")

    def test_sliding_window_expires_old_entries(self):
        """Old entries outside the window should not count toward the limit."""
        if not self.redis_available:
            self.skipTest('Redis not available')

        # Use a very short window (1 second)
        for i in range(5):
            self.limiter.is_allowed(
                key=self.test_key, limit=5, window_seconds=1,
                request_id=f'old-{i}',
            )

        # At limit now — next request should be denied
        result = self.limiter.is_allowed(
            key=self.test_key, limit=5, window_seconds=1,
            request_id='denied',
        )
        self.assertFalse(result)

        # Wait for the window to expire
        time.sleep(1.1)

        # Now the same limit should allow requests again
        result = self.limiter.is_allowed(
            key=self.test_key, limit=5, window_seconds=1,
            request_id='new-after-expire',
        )
        self.assertTrue(result, "Requests should be allowed after window expires")

    def test_get_current_count(self):
        """get_current_count should return accurate count within the window."""
        if not self.redis_available:
            self.skipTest('Redis not available')

        for i in range(7):
            self.limiter.is_allowed(
                key=self.test_key, limit=100, window_seconds=60,
                request_id=f'count-{i}',
            )

        count = self.limiter.get_current_count(self.test_key, 60)
        self.assertEqual(count, 7)


class CeleryTaskTest(TestCase):
    """
    Tests for the Celery email task — uses CELERY_TASK_ALWAYS_EAGER
    so tasks run synchronously in-process (no broker needed).
    """

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_500_jobs_no_job_lost(self):
        """
        Submit 500 jobs — assert none are lost.
        All should either succeed or land in FailedJob.
        """
        from notifications.tasks import send_email_notification

        succeeded = 0
        failed_count_before = FailedJob.objects.count()

        # Mock the rate limiter to always allow (we test rate limiting separately)
        with patch('notifications.tasks.get_rate_limiter') as mock_rl:
            mock_limiter = MagicMock()
            mock_limiter.is_allowed.return_value = True
            mock_rl.return_value = mock_limiter

            for i in range(500):
                result = send_email_notification.apply(
                    args=[f'user{i}@test.com', f'Subject {i}', f'Message {i}'],
                )
                if result.result and result.result.get('status') == 'sent':
                    succeeded += 1

        failed_count_after = FailedJob.objects.count()
        new_failures = failed_count_after - failed_count_before

        # Every job must either succeed or be in FailedJob — none lost
        self.assertEqual(
            succeeded + new_failures, 500,
            f"Jobs lost! {succeeded} succeeded + {new_failures} failed != 500"
        )

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    def test_forced_failure_creates_dead_letter(self):
        """
        A task with force_fail=True should exhaust retries and create
        a FailedJob entry (dead-letter handling).
        """
        from notifications.tasks import send_email_notification

        failed_before = FailedJob.objects.count()

        # Mock rate limiter to always allow
        with patch('notifications.tasks.get_rate_limiter') as mock_rl:
            mock_limiter = MagicMock()
            mock_limiter.is_allowed.return_value = True
            mock_rl.return_value = mock_limiter

            result = send_email_notification.apply(
                args=['fail@test.com', 'Will Fail', 'This should fail'],
                kwargs={'force_fail': True},
            )

        failed_after = FailedJob.objects.count()
        self.assertEqual(
            failed_after, failed_before + 1,
            "A permanently failed task should create exactly one FailedJob entry"
        )

        # Verify the FailedJob has correct data
        failed_job = FailedJob.objects.latest('failed_at')
        self.assertIn('fail@test.com', failed_job.args[0])
        self.assertIn('Forced failure', failed_job.exception)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_successful_send_returns_sent_status(self):
        """A successful email task should return status='sent'."""
        from notifications.tasks import send_email_notification

        with patch('notifications.tasks.get_rate_limiter') as mock_rl:
            mock_limiter = MagicMock()
            mock_limiter.is_allowed.return_value = True
            mock_rl.return_value = mock_limiter

            result = send_email_notification.apply(
                args=['success@test.com', 'Hello', 'World'],
            )

        self.assertEqual(result.result['status'], 'sent')
        self.assertEqual(result.result['to'], 'success@test.com')
