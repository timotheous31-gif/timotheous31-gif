"""Investigation run and job endpoints.

Two of these routes take a job id and no case id, which made them the platform's
most exposed surface: a job UUID alone was enough to read an investigation's
progress, or to stop it, in anybody's workspace. ``CurrentJob`` resolves
job -> case -> membership so the URLs are unchanged and the authorization is not
optional.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    AppSettings,
    CaseContext,
    CurrentUser,
    DbSession,
    JobContext,
    accessible_workspace_ids,
    require,
    require_job,
)
from app.core import throttle
from app.core.errors import ThrottledError
from app.core.permissions import Permission
from app.models.enums import AuditEvent
from app.schemas.finding import JobRead, RunRequest, RunResponse
from app.services import audit
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
    ctx: Annotated[CaseContext, Depends(require(Permission.INVESTIGATION_RUN))],
    session: DbSession,
    settings: AppSettings,
    payload: RunRequest | None = None,
) -> RunResponse:
    """Queue an investigation for the case.

    The job is handed to a Celery worker when one is reachable and executed
    inline otherwise; the response says which happened rather than leaving the
    caller to guess.

    Throttled per user. An investigation is the most expensive thing this platform
    does — every collector, every outbound request, and on a paid provider, real
    money — so it is the one a runaway script must not be able to repeat freely.
    """
    verdict = throttle.check(
        throttle.principal_key("investigation", str(ctx.principal.user_id)),
        limit=settings.rate_limit_investigation_per_hour,
        window_seconds=3600,
        settings=settings,
    )
    if verdict.refused:
        raise ThrottledError(
            "You have started a lot of investigations in a short time. They are "
            "expensive to run, so there is an hourly ceiling. Try again shortly.",
            retry_after=verdict.retry_after,
        )

    options = payload or RunRequest()
    job = job_service.create_job(
        session,
        ctx.case_id,
        include_collectors=options.collectors,
        exclude_collectors=options.exclude_collectors,
        target_ids=list(options.target_ids),
    )
    audit.record(
        session,
        event=AuditEvent.INVESTIGATION_STARTED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="job",
        object_id=job.id,
        metadata={"case_id": str(ctx.case_id), "collectors": len(options.collectors or [])},
    )
    session.commit()

    dispatch = job_service.dispatch_job(session, job)
    session.commit()
    session.refresh(job)
    return RunResponse(job=_read([job])[0], dispatch=dispatch)


@run_router.get("/jobs", response_model=list[JobRead], summary="List a case's jobs")
def list_case_jobs(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
) -> list[JobRead]:
    return _read(job_service.list_jobs(session, ctx.case_id))


@router.get("/{job_id}", response_model=JobRead, summary="Job status and progress")
def get_job(ctx: Annotated[JobContext, Depends(require_job(Permission.CASE_READ))]) -> JobRead:
    return _read([ctx.job])[0]


@router.post("/{job_id}/cancel", response_model=JobRead, summary="Request cancellation")
def cancel_job(
    ctx: Annotated[JobContext, Depends(require_job(Permission.INVESTIGATION_CANCEL))],
    session: DbSession,
) -> JobRead:
    """Ask a running investigation to stop; the worker checks between collectors."""
    job = job_service.request_cancel(session, ctx.job.id)  # type: ignore[attr-defined]
    audit.record(
        session,
        event=AuditEvent.INVESTIGATION_CANCELLED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="job",
        object_id=job.id,
        metadata={"case_id": str(ctx.case_id)},
    )
    session.commit()
    return _read([job])[0]


@router.get("", response_model=list[JobRead], summary="List recent jobs")
def list_jobs(
    principal: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JobRead]:
    """Recent jobs across the caller's workspaces, and no others.

    Filtered in the query rather than after it. A cross-workspace job listing was
    the quietest of the IDOR holes here: it disclosed other customers' case ids,
    run times and progress without ever naming a case.
    """
    return _read(
        job_service.list_jobs(
            session,
            limit=limit,
            workspace_ids=accessible_workspace_ids(session, principal.user_id),
        )
    )
