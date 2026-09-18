"""Case management endpoints.

Every route here is workspace-scoped. ``CurrentCase`` resolves the case and the
caller's membership together, so a case in another workspace is a 404 rather than
a 403 — a caller who may not have it must not learn that it exists.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    AppSettings,
    CaseContext,
    CurrentUser,
    DbSession,
    accessible_workspace_ids,
    require,
)
from app.core import throttle
from app.core.errors import NotFoundError, ThrottledError
from app.core.permissions import Permission
from app.models.enums import AuditEvent, CaseStatus
from app.schemas.case import (
    CaseCreate,
    CaseRead,
    CaseSummary,
    CaseUpdate,
)
from app.schemas.common import Page
from app.services import audit
from app.services import cases as case_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post(
    "",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an investigation case",
)
def create_case(
    payload: CaseCreate,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> CaseRead:
    """Create a case inside one of the caller's workspaces.

    ``workspace_id`` on the payload is optional only when the caller belongs to
    exactly one workspace; with several it must be named, because guessing which
    one a case belongs to is guessing who may read it.
    """
    from app.core.permissions import allows
    from app.services import accounts

    memberships = accounts.memberships_for_user(session, principal.user_id)
    if not memberships:
        raise NotFoundError("You do not belong to a workspace yet")
    if payload.workspace_id is not None:
        membership = next(
            (item for item in memberships if item.workspace_id == payload.workspace_id), None
        )
        if membership is None:
            raise NotFoundError("No such workspace")
    elif len(memberships) == 1:
        membership = memberships[0]
    else:
        raise NotFoundError(
            "You belong to more than one workspace, so this request must name the "
            "workspace_id the case belongs to."
        )
    if not allows(membership.role, Permission.CASE_CREATE):
        from app.core.errors import PermissionDenied

        raise PermissionDenied(
            f"Your role in this workspace ({membership.role}) does not permit " f"creating cases."
        )

    verdict = throttle.check(
        throttle.principal_key("case-create", str(principal.user_id)),
        limit=settings.rate_limit_case_create_per_hour,
        window_seconds=3600,
        settings=settings,
    )
    if verdict.refused:
        raise ThrottledError(
            "You have created a lot of cases in a short time. Try again shortly.",
            retry_after=verdict.retry_after,
        )

    case = case_service.create_case(session, payload, workspace_id=membership.workspace_id)
    audit.record(
        session,
        event=AuditEvent.CASE_CREATED,
        actor_user_id=principal.user_id,
        workspace_id=membership.workspace_id,
        object_type="case",
        object_id=case.id,
        metadata={"name": case.name},
    )
    session.commit()
    return CaseRead.model_validate(case)


@router.get("", response_model=Page[CaseRead], summary="List cases")
def list_cases(
    principal: CurrentUser,
    session: DbSession,
    status_filter: CaseStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="Case-name substring"),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CaseRead]:
    """Cases in the caller's workspaces, and no others."""
    items, total = case_service.list_cases(
        session,
        status=status_filter,
        query=q,
        tag=tag,
        limit=limit,
        offset=offset,
        workspace_ids=accessible_workspace_ids(session, principal.user_id),
    )
    return Page[CaseRead](
        items=[CaseRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{case_id}", response_model=CaseRead, summary="Fetch one case")
def get_case(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
) -> CaseRead:
    return CaseRead.model_validate(ctx.case)


@router.get("/{case_id}/summary", response_model=CaseSummary, summary="Case overview counters")
def get_case_summary(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
) -> CaseSummary:
    """Everything the dashboard's case-overview page needs in one request."""
    data = case_service.case_summary(session, ctx.case_id)
    return CaseSummary.model_validate({**data, "case": CaseRead.model_validate(data["case"])})


@router.patch("/{case_id}", response_model=CaseRead, summary="Update a case")
def update_case(
    payload: CaseUpdate,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_UPDATE))],
    session: DbSession,
) -> CaseRead:
    case = case_service.update_case(session, ctx.case_id, payload)
    session.commit()
    return CaseRead.model_validate(case)


@router.delete(
    "/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a case and its data"
)
def delete_case(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_DELETE))],
    session: DbSession,
) -> None:
    """Delete the case and every finding, entity and evidence record it holds."""
    case_service.delete_case(session, ctx.case_id)
    audit.record(
        session,
        event=AuditEvent.CASE_DELETED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="case",
        object_id=ctx.case_id,
        metadata={"name": ctx.case.name},
    )
    session.commit()
