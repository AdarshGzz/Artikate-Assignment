# config/__init__.py
# Import Celery app so that it is loaded when Django starts.
from .celery import app as celery_app

__all__ = ('celery_app',)
