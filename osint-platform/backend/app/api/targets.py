"""Target management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, status

from app.api.deps import CaseId, DbSession, parse_uuid
from app.models.enums import TargetType
from app.schemas.case import (
    NormalizationPreview,
    TargetBulkCreate,
    TargetCreate,
    TargetRead,
    TargetUpdate,
)
from app.schemas.common import Page
from app.services import cases as case_service
from app.services.normalization import normalize_target

router = APIRouter(prefix="/cases/{case_id}/targets", tags=["targets"])


@router.post(
    "",
    response_model=TargetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a target to a case",
)
def add_target(case_id: CaseId, payload: TargetCreate, session: DbSession) -> TargetRead:
    """Normalise and attach a target.

    The target type is inferred from the input's shape when it is not supplied.
    """
    target = case_service.add_target(session, case_id, payload)
    session.commit()
    return TargetRead.model_validate(target)


@router.post(
    "/bulk",
    response_model=list[TargetRead],
    status_code=status.HTTP_201_CREATED,
    summary="Add several targets at once",
)
def add_targets(case_id: CaseId, payload: TargetBulkCreate, session: DbSession) -> list[TargetRead]:
    """Add many targets. Duplicates within the case are skipped, not fatal."""
    from app.core.errors import ConflictError

    created: list[TargetRead] = []
    for item in payload.targets:
        try:
            target = case_service.add_target(session, case_id, item)
        except ConflictError:
            continue
        created.append(TargetRead.model_validate(target))
    session.commit()
    return created


@router.get("", response_model=Page[TargetRead], summary="List a case's targets")
def list_targets(
    case_id: CaseId,
    session: DbSession,
    target_type: TargetType | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[TargetRead]:
    items, total = case_service.list_targets(
        session, case_id, target_type=target_type, limit=limit, offset=offset
    )
    return Page[TargetRead](
        items=[TargetRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{target_id}", response_model=TargetRead, summary="Fetch one target")
def get_target(case_id: CaseId, target_id: str, session: DbSession) -> TargetRead:
    target = case_service.get_target(session, case_id, parse_uuid(target_id, "target_id"))
    return TargetRead.model_validate(target)


@router.patch("/{target_id}", response_model=TargetRead, summary="Update a target")
def update_target(
    case_id: CaseId, target_id: str, payload: TargetUpdate, session: DbSession
) -> TargetRead:
    target = case_service.update_target(
        session, case_id, parse_uuid(target_id, "target_id"), payload
    )
    session.commit()
    return TargetRead.model_validate(target)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a target")
def delete_target(case_id: CaseId, target_id: str, session: DbSession) -> None:
    case_service.delete_target(session, case_id, parse_uuid(target_id, "target_id"))
    session.commit()


@router.post(
    "/preview",
    response_model=NormalizationPreview,
    summary="Preview how an input would be normalised",
)
def preview_normalization(
    case_id: CaseId,
    value: str = Body(embed=True, max_length=1024),
    target_type: TargetType | None = Body(default=None, embed=True, alias="type"),
) -> NormalizationPreview:
    """Show the stored form of a raw input without creating anything."""
    normalized = normalize_target(value, target_type)
    return NormalizationPreview(
        raw_input=normalized.raw_input,
        type=normalized.type,
        normalized_value=normalized.value,
        attributes=normalized.attributes,
    )
