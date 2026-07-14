# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Celery task: send_email_notification with retry + dead-letter
# See DESIGN.md for architecture decisions
# See ANSWERS.md for SIGKILL behavior explanation
# ============================================

import logging
import traceback as tb

from celery import shared_task
from django.conf import settings

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Rate limiter instance (shared across task invocations in the same worker)
_rate_limiter = None


def get_rate_limiter():
    """Lazy-initialize the rate limiter (avoids import-time Redis connection)."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(redis_url=settings.CELERY_BROKER_URL)
    return _rate_limiter


class EmailSendError(Exception):
    """Raised when email sending fails (simulated)."""
    pass


@shared_task(
    bind=True,
    max_retries=5,
    acks_late=True,                   # Don't ack until task completes (SIGKILL safety)
    reject_on_worker_lost=True,       # Re-queue if worker is killed mid-task
    default_retry_delay=10,
    name='notifications.send_email',
)
def send_email_notification(self, to_email, subject, message, force_fail=False):
    """
    Send an email notification with rate limiting, exponential backoff retry,
    and dead-letter handling for permanent failures.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        message: Email body text
        force_fail: If True, always raises an error (for testing retry logic)

    Rate Limiting:
        Checks the sliding window rate limiter (200 emails/minute) before sending.
        If rate limited, the task retries with a short countdown (no time.sleep).

    Retry Strategy:
        Exponential backoff: 10s, 20s, 40s, 80s, 160s (base * 2^retry_count).
        After 5 retries, the task is written to the FailedJob dead-letter table.
    """
    rate_limiter = get_rate_limiter()

    # Check rate limit (200 per minute)
    if not rate_limiter.is_allowed(
        key='email:rate_limit',
        limit=200,
        window_seconds=60,
        request_id=self.request.id or f'task-{id(self)}',
    ):
        # Rate limited — retry after a short delay (NOT time.sleep)
        logger.info(f"Rate limited, retrying task {self.request.id}")
        raise self.retry(countdown=5, max_retries=20)  # More retries for rate limiting

    try:
        # Simulate email sending
        if force_fail:
            raise EmailSendError(f"Forced failure for testing: {to_email}")

        # In production, this would call an email provider API (SendGrid, SES, etc.)
        # For this assessment, we simulate successful sending.
        logger.info(f"Email sent to {to_email}: {subject}")

        return {
            'status': 'sent',
            'to': to_email,
            'subject': subject,
        }

    except EmailSendError as exc:
        retries = self.request.retries
        if retries >= self.max_retries:
            # All retries exhausted — write to dead-letter table
            _store_failed_job(self, to_email, subject, message, exc)
            logger.error(
                f"Email to {to_email} permanently failed after {retries} retries: {exc}"
            )
            return {
                'status': 'failed',
                'to': to_email,
                'reason': str(exc),
            }

        # Exponential backoff: base_delay * 2^retry_count
        backoff_delay = 10 * (2 ** retries)
        logger.warning(
            f"Email to {to_email} failed (attempt {retries + 1}/{self.max_retries}), "
            f"retrying in {backoff_delay}s: {exc}"
        )
        raise self.retry(exc=exc, countdown=backoff_delay)


def _store_failed_job(task, to_email, subject, message, exception):
    """Write a permanently failed task to the FailedJob dead-letter table."""
    from .models import FailedJob

    FailedJob.objects.create(
        task_id=task.request.id or 'unknown',
        task_name=task.name,
        args=[to_email, subject, message],
        kwargs={},
        exception=str(exception),
        traceback=tb.format_exc(),
    )
