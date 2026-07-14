# ============================================
# SECTION 2 — Rate-Limited Async Job Queue
# URL configuration for notifications API
# ============================================

from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('send/', views.send_notification, name='send'),
    path('status/', views.queue_status, name='status'),
]
