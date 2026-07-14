# ============================================
# SECTION 1 — N+1 Query Diagnosis
# URL configuration for orders API
# ============================================

from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    # Section 1: N+1 diagnosis endpoints
    path('slow/', views.slow_order_list, name='slow-list'),
    path('fast/', views.fast_order_list, name='fast-list'),
]
