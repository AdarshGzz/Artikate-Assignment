# ============================================
# SECTION 3 — Multi-Tenant Data Isolation
# TenantManager: auto-filters querysets by tenant
# See ANSWERS.md for async failure mode explanation
# ============================================

from django.db import models
from .tenant import get_current_tenant


class TenantManager(models.Manager):
    """
    Custom manager that automatically filters querysets by the current tenant.

    When a tenant is set (via middleware), all queries through this manager
    will only return rows belonging to that tenant. If no tenant is set,
    returns an empty queryset (fail-closed — prevents accidental data leaks).
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant_id = get_current_tenant()
        if tenant_id is not None:
            return qs.filter(tenant=tenant_id)
        # Fail-closed: no tenant = no data (never leak all tenants' data)
        return qs.none()
