"""Celery application and tasks."""

from __future__ import annotations

__all__ = ["celery_app"]


def __getattr__(name: str) -> object:
    """Import the Celery app lazily so the API does not need a broker to start."""
    if name == "celery_app":
        from app.workers.celery_app import celery_app

        return celery_app
    raise AttributeError(name)
