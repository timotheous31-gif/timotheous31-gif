"""Case management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CaseId, DbSession
from app.models.enums import CaseStatus
from app.schemas.case import (
    CaseCreate,
    CaseRead,
    CaseSummary,
    CaseUpdate,
)
from app.schemas.common import Page
from app.services import cases as case_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post(
    "",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an investigation case",
)
def create_case(payload: CaseCreate, session: DbSession) -> CaseRead:
    """Create a case. Cases group targets, findings, entities and evidence."""
    case = case_service.create_case(session, payload)
    session.commit()
    return CaseRead.model_validate(case)


@router.get("", response_model=Page[CaseRead], summary="List cases")
def list_cases(
    session: DbSession,
    status_filter: CaseStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="Case-name substring"),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CaseRead]:
    items, total = case_service.list_cases(
        session, status=status_filter, query=q, tag=tag, limit=limit, offset=offset
    )
    return Page[CaseRead](
        items=[CaseRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{case_id}", response_model=CaseRead, summary="Fetch one case")
def get_case(case_id: CaseId, session: DbSession) -> CaseRead:
    return CaseRead.model_validate(case_service.get_case(session, case_id))


@router.get("/{case_id}/summary", response_model=CaseSummary, summary="Case overview counters")
def get_case_summary(case_id: CaseId, session: DbSession) -> CaseSummary:
    """Everything the dashboard's case-overview page needs in one request."""
    data = case_service.case_summary(session, case_id)
    return CaseSummary.model_validate({**data, "case": CaseRead.model_validate(data["case"])})


@router.patch("/{case_id}", response_model=CaseRead, summary="Update a case")
def update_case(case_id: CaseId, payload: CaseUpdate, session: DbSession) -> CaseRead:
    case = case_service.update_case(session, case_id, payload)
    session.commit()
    return CaseRead.model_validate(case)


@router.delete(
    "/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a case and its data"
)
def delete_case(case_id: CaseId, session: DbSession) -> None:
    """Delete the case and every finding, entity and evidence record it holds."""
    case_service.delete_case(session, case_id)
    session.commit()
