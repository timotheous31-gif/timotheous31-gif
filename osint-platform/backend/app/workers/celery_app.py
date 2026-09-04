"""Celery application.

Configuration favours correctness over throughput: late acknowledgement so a
worker crash re-queues the investigation, no prefetch hoarding, and hard time
limits so a wedged collector cannot occupy a worker forever.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging

from app.core.logging import configure_logging
from app.core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "osint",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    #: An investigation is bounded by collector timeouts; these are the backstop.
    task_soft_time_limit=1800,
    task_time_limit=2100,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
)


@setup_logging.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    """Use the platform's structured logging inside workers too."""
    configure_logging()
