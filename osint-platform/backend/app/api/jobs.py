"""Investigation run and job endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CaseId, DbSession, parse_uuid
from app.schemas.finding import JobRead, RunRequest, RunResponse
from app.services import jobs as job_service

run_router = APIRouter(prefix="/cases/{case_id}", tags=["investigations"])
router = APIRouter(prefix="/jobs", tags=["investigations"])


def _read(jobs: list) -> list[JobRead]:
    """Serialise jobs, resolving the worker state once for the whole page.

    The broker is probed only when something is actually QUEUED: a list of
    finished jobs tells us nothing about workers and should not pay for a ping
    on every poll.
    """
    from app.models.enums import JobState

    workers = None
    if any(job.state is JobState.QUEUED for job in jobs):
        workers = job_service.worker_status()

    output = []
    for job in jobs:
        read = JobRead.model_validate(job)
        read.effective_state = job_service.effective_state(job, workers)
        read.processing_available = workers.available if workers is not None else True
        output.append(read)
    return output


@run_router.post(
    "/run",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an investigation",
)
def run_investigation(
    case_id: CaseId, session: DbSession, payload: RunRequest | None = None
) -> RunResponse:
    """Queue an investigation for the case.

    The job is handed to a Celery worker when one is reachable and executed
    inline otherwise; the response says which happened rather than leaving the
    caller to guess.
    """
    options = payload or RunRequest()
    job = job_service.create_job(
        session,
        case_id,
        include_collectors=options.collectors,
        exclude_collectors=options.exclude_collectors,
        target_ids=list(options.target_ids),
    )
    session.commit()

    dispatch = job_service.dispatch_job(session, job)
    session.commit()
    session.refresh(job)
    return RunResponse(job=_read([job])[0], dispatch=dispatch)


@run_router.get("/jobs", response_model=list[JobRead], summary="List a case's jobs")
def list_case_jobs(case_id: CaseId, session: DbSession) -> list[JobRead]:
    return _read(job_service.list_jobs(session, case_id))


@router.get("/{job_id}", response_model=JobRead, summary="Job status and progress")
def get_job(job_id: str, session: DbSession) -> JobRead:
    return _read([job_service.get_job(session, parse_uuid(job_id, "job_id"))])[0]


@router.post("/{job_id}/cancel", response_model=JobRead, summary="Request cancellation")
def cancel_job(job_id: str, session: DbSession) -> JobRead:
    """Ask a running investigation to stop; the worker checks between collectors."""
    job = job_service.request_cancel(session, parse_uuid(job_id, "job_id"))
    session.commit()
    return _read([job])[0]


@router.get("", response_model=list[JobRead], summary="List recent jobs")
def list_jobs(session: DbSession, limit: int = Query(default=50, ge=1, le=200)) -> list[JobRead]:
    return _read(job_service.list_jobs(session, limit=limit))
