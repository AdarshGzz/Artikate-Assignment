# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Models: FailedJob (dead-letter storage)
# See DESIGN.md for architecture decisions
# ============================================

from django.db import models


class FailedJob(models.Model):
    """
    Dead-letter storage for Celery tasks that permanently fail after
    all retry attempts are exhausted.

    When a task exceeds max_retries, it writes itself here instead of
    silently disappearing. This is the simplest form of dead-letter
    handling — no admin UI, just a DB table that can be queried.
    """
    task_id = models.CharField(max_length=255, unique=True)
    task_name = models.CharField(max_length=255)
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    exception = models.TextField()
    traceback = models.TextField(blank=True, default='')
    failed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"FailedJob {self.task_id} ({self.task_name})"

    class Meta:
        ordering = ['-failed_at']
