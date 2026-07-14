"""
config URL Configuration

Root URL configuration for the Artikate Studio Backend Assessment.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/orders/', include('orders.urls')),
    path('api/notifications/', include('notifications.urls')),
]

# Django Debug Toolbar URLs (only in DEBUG mode)
if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns
