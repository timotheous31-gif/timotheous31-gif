"""Celery tasks.

Tasks are thin: they open a session, delegate to the same service functions the
API and CLI use, and make sure the job row reflects what happened even when the
task itself fails.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger, request_id_var
from app.services import jobs as job_service

log = get_logger(__name__)


@shared_task(bind=True, name="osint.run_investigation", max_retries=0)
def run_investigation_task(self: Any, job_id: str) -> dict[str, Any]:
    """Execute the investigation recorded under ``job_id``."""
    configure_logging()
    token = request_id_var.set(str(self.request.id or job_id))
    parsed = uuid.UUID(job_id)
    try:
        with session_scope() as session:
            job_service.start_job(session, parsed, celery_id=str(self.request.id or ""))
        with session_scope() as session:
            result = job_service.execute_job(session, parsed)
        return result.as_dict()
    except SoftTimeLimitExceeded as exc:
        with session_scope() as session:
            job_service.fail_job(session, parsed, exc)
        log.error("task.time_limit_exceeded", job_id=job_id)
        raise
    except Exception as exc:
        # execute_job already marks the job failed for its own errors; this
        # covers failures outside it (session setup, serialisation).
        with session_scope() as session:
            job = session.get(_job_model(), parsed)
            if job is not None and job.state not in job_service.TERMINAL_STATES:
                job_service.fail_job(session, parsed, exc)
        log.exception("task.failed", job_id=job_id)
        raise
    finally:
        request_id_var.reset(token)


@shared_task(name="osint.ping")
def ping() -> str:
    """Liveness probe for the worker."""
    return "pong"


def _job_model() -> type:
    from app.models import Job

    return Job
