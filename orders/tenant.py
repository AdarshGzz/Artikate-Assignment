# ============================================
# SECTION 3 — Multi-Tenant Data Isolation
# Thread-local storage for current tenant context
# See ANSWERS.md for async failure mode explanation
# ============================================

import threading

_thread_locals = threading.local()


def set_current_tenant(tenant_id):
    """Set the current tenant for this thread/request."""
    _thread_locals.tenant_id = tenant_id


def get_current_tenant():
    """Get the current tenant for this thread/request. Returns None if not set."""
    return getattr(_thread_locals, 'tenant_id', None)


def clear_current_tenant():
    """
    Clear the tenant context after request processing.
    CRITICAL: Must be called in a finally block to prevent tenant leaking
    between requests on the same thread (thread reuse in WSGI servers).
    """
    _thread_locals.tenant_id = None
