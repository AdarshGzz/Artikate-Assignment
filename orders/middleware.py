# ============================================
# SECTION 3 — Multi-Tenant Data Isolation
# Middleware: extracts tenant from request, sets/clears context
# See ANSWERS.md for async failure mode explanation
# ============================================

from .tenant import set_current_tenant, clear_current_tenant


class TenantMiddleware:
    """
    Extracts tenant identifier from the request and sets it in thread-local
    storage for the duration of the request.

    Tenant resolution order:
    1. X-Tenant-ID header (for API clients / testing)
    2. Subdomain extraction (e.g., acme.artikate.com → 'acme')

    CRITICAL: Always clears the tenant in a finally block to prevent
    leaking between requests on reused threads (WSGI thread pools).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = self._resolve_tenant(request)
        set_current_tenant(tenant_id)
        try:
            response = self.get_response(request)
        finally:
            # Always clean up — even if the view raises an exception
            clear_current_tenant()
        return response

    def _resolve_tenant(self, request):
        """
        Resolve tenant ID from the request.
        Priority: X-Tenant-ID header > subdomain > None
        """
        # 1. Check explicit header (primary method for API usage and testing)
        tenant_header = request.META.get('HTTP_X_TENANT_ID')
        if tenant_header:
            return tenant_header

        # 2. Extract from subdomain (e.g., acme.artikate.com → 'acme')
        host = request.get_host().split(':')[0]  # strip port
        parts = host.split('.')
        if len(parts) > 2:
            subdomain = parts[0]
            if subdomain not in ('www', 'api'):
                return subdomain

        return None
