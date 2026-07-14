# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# Helper script to submit jobs for screen recording
# ============================================

import json
import urllib.request
import time

API_URL = "http://127.0.0.1:8000/api/notifications/send/"

def send_request(payload):
    req = urllib.request.Request(
        API_URL, 
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        print(f"Error sending request: {e}")
        return None, None

def main():
    print("🚀 Starting Demo Job Submission...")
    
    # 1. Submit 249 successful jobs to trigger rate limiting (limit is 200/min)
    print("\n1. Submitting 249 successful jobs...")
    for i in range(1, 250):
        payload = {
            "to": f"user{i}@example.com",
            "subject": f"Welcome Email {i}",
            "message": f"Hello user {i}, welcome to Artikate!"
        }
        status, resp = send_request(payload)
        if i % 50 == 0 or i == 249:
            print(f"   Submitted job {i}/249 - Task ID: {resp.get('task_id') if resp else 'None'}")
            
    # 2. Submit 1 job that is forced to fail to show exponential backoff
    print("\n2. Submitting 1 job that is forced to fail (to demonstrate backoff and retry)...")
    payload = {
        "to": "fail-user@example.com",
        "subject": "Failing Job Alert",
        "message": "This job will fail on purpose.",
        "force_fail": True  # This triggers simulated EmailSendError in tasks.py
    }
    status, resp = send_request(payload)
    print(f"   Submitted failing job - Task ID: {resp.get('task_id') if resp else 'None'}")
    
    print("\n✅ All 250 test jobs submitted successfully!")
    print("Check your Celery logs to see the rate limiter and retries in action!")

if __name__ == "__main__":
    main()
