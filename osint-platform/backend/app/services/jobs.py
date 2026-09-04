"""Job lifecycle.

A job mirrors one investigation run into the database so the API can report
progress, and so a cancellation request has somewhere to live. The database is
the source of truth rather than Celery's result backend: the UI must be able to
show a run's state even if the broker has been restarted.

When Celery is unreachable the job is executed inline. That is deliberate — the
CLI and small deployments should work without a broker — and the job records
which path it took.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models import Case, Job
from app.models.enums import JobState
from app.services.engine import InvestigationOptions, InvestigationResult, run_investigation

log = get_logger(__name__)

#: Job states that mean the job has finished, one way or another.
TERMINAL_STATES = {JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED}


def create_job(
    session: Session,
    case_id: uuid.UUID,
    *,
    include_collectors: list[str] | None = None,
    exclude_collectors: list[str] | None = None,
    target_ids: list[uuid.UUID] | None = None,
) -> Job:
    """Record a queued investigation for ``case_id``."""
    case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} does not exist")

    running = session.scalar(
        select(Job).where(
            Job.case_id == case_id, Job.state.in_([JobState.QUEUED, JobState.RUNNING])
        )
    )
    if running is not None:
        raise ConflictError(
            f"Case {case_id} already has an active investigation",
            detail={"job_id": str(running.id), "state": str(running.state)},
        )

    job = Job(
        case_id=case_id,
        state=JobState.QUEUED,
        progress=0.0,
        message="Queued",
        params={
            "include_collectors": include_collectors or [],
            "exclude_collectors": exclude_collectors or [],
            "target_ids": [str(item) for item in (target_ids or [])],
        },
    )
    session.add(job)
    session.flush()
    log.info("job.created", job_id=str(job.id), case_id=str(case_id))
    return job


def get_job(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} does not exist")
    return job


def list_jobs(session: Session, case_id: uuid.UUID | None = None, limit: int = 50) -> list[Job]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    if case_id is not None:
        stmt = stmt.where(Job.case_id == case_id)
    return list(session.scalars(stmt))


def request_cancel(session: Session, job_id: uuid.UUID) -> Job:
    """Ask a running job to stop. The worker checks between collectors."""
    job = get_job(session, job_id)
    if job.state in TERMINAL_STATES:
        raise ConflictError(f"Job {job_id} has already finished ({job.state})")
    job.cancel_requested = True
    if job.state is JobState.QUEUED:
        # Never started, so it can be finalised immediately.
        job.state = JobState.CANCELLED
        job.finished_at = datetime.now(UTC)
        job.message = "Cancelled before it started"
    else:
        job.message = "Cancellation requested"
    session.flush()
    log.info("job.cancel_requested", job_id=str(job_id), state=str(job.state))
    return job


def is_cancelled(session: Session, job_id: uuid.UUID) -> bool:
    """Fresh read of the cancellation flag (the worker polls this)."""
    session.expire_all()
    job = session.get(Job, job_id)
    return bool(job and job.cancel_requested)


def update_progress(session: Session, job_id: uuid.UUID, fraction: float, message: str) -> None:
    """Record progress. Never raises — progress must not fail a run."""
    try:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.progress = max(0.0, min(1.0, fraction))
        job.message = message[:500]
        if job.state is JobState.QUEUED:
            job.state = JobState.RUNNING
        session.flush()
    except Exception:
        log.warning("job.progress_update_failed", job_id=str(job_id))


def start_job(session: Session, job_id: uuid.UUID, celery_id: str | None = None) -> Job:
    job = get_job(session, job_id)
    job.state = JobState.RUNNING
    job.started_at = datetime.now(UTC)
    job.message = "Running"
    if celery_id:
        job.celery_id = celery_id
    session.flush()
    return job


def finish_job(session: Session, job_id: uuid.UUID, result: InvestigationResult) -> Job:
    job = get_job(session, job_id)
    job.state = JobState.CANCELLED if result.cancelled else JobState.COMPLETE
    job.progress = 1.0
    job.finished_at = datetime.now(UTC)
    job.result = result.as_dict()
    job.message = (
        "Cancelled"
        if result.cancelled
        else f"Complete: {result.findings_created} finding(s), "
        f"{result.entities_created} entity(ies)"
    )
    session.flush()
    log.info("job.finished", job_id=str(job_id), state=str(job.state))
    return job


def fail_job(session: Session, job_id: uuid.UUID, exc: BaseException) -> Job:
    job = get_job(session, job_id)
    job.state = JobState.FAILED
    job.finished_at = datetime.now(UTC)
    job.error_type = type(exc).__name__
    job.error_message = str(exc)[:2000]
    job.message = f"Failed: {type(exc).__name__}"
    session.flush()
    log.error("job.failed", job_id=str(job_id), error_type=type(exc).__name__)
    return job


def execute_job(session: Session, job_id: uuid.UUID) -> InvestigationResult:
    """Run a job to completion in this process."""
    job = get_job(session, job_id)
    start_job(session, job_id)
    session.commit()

    options = InvestigationOptions(
        include_collectors=list(job.params.get("include_collectors") or []),
        exclude_collectors=list(job.params.get("exclude_collectors") or []),
        target_ids=[uuid.UUID(item) for item in (job.params.get("target_ids") or [])],
        should_cancel=lambda: is_cancelled(session, job_id),
        on_progress=lambda fraction, message: update_progress(session, job_id, fraction, message),
    )
    try:
        result = run_investigation(session, job.case_id, options, job=job)
    except Exception as exc:
        session.rollback()
        fail_job(session, job_id, exc)
        session.commit()
        raise
    finish_job(session, job_id, result)
    session.commit()
    return result


def dispatch_job(session: Session, job: Job) -> dict[str, Any]:
    """Hand the job to a Celery worker, or run it inline if none is reachable.

    Returns a description of what happened so the API can tell the caller which
    path was taken instead of leaving them guessing.
    """
    settings = get_settings()
    if settings.celery_task_always_eager:
        execute_job(session, job.id)
        return {"dispatched": "inline", "reason": "CELERY_TASK_ALWAYS_EAGER is set"}

    try:
        from app.workers.tasks import run_investigation_task

        async_result = run_investigation_task.apply_async(args=[str(job.id)])
    except Exception as exc:
        log.warning(
            "job.dispatch_failed_running_inline",
            job_id=str(job.id),
            error_type=type(exc).__name__,
        )
        execute_job(session, job.id)
        return {
            "dispatched": "inline",
            "reason": f"Celery broker unreachable ({type(exc).__name__})",
        }

    job.celery_id = str(async_result.id)
    session.flush()
    return {"dispatched": "celery", "celery_id": job.celery_id}
