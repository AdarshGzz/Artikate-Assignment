# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Views: submit notification jobs, check queue status
# See DESIGN.md for architecture decisions
# ============================================

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET


@csrf_exempt
@require_POST
def send_notification(request):
    """Submit an email notification job to the Celery queue."""
    from .tasks import send_email_notification

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    to_email = body.get('to')
    subject = body.get('subject', 'Notification')
    message = body.get('message', '')

    if not to_email:
        return JsonResponse({'error': 'Missing "to" field'}, status=400)

    result = send_email_notification.delay(to_email, subject, message)

    return JsonResponse({
        'status': 'queued',
        'task_id': result.id,
    }, status=202)


@require_GET
def queue_status(request):
    """Check overall queue status (basic)."""
    from .models import FailedJob
    failed_count = FailedJob.objects.count()
    return JsonResponse({
        'failed_jobs': failed_count,
    })
