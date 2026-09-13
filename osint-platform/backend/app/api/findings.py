"""Finding, evidence and collector-run endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import CaseContext, DbSession, require
from app.core.permissions import Permission
from app.models import CollectorRun, Evidence, Finding
from app.models.enums import Classification, FindingKind
from app.schemas.common import Page
from app.schemas.finding import CollectorRunRead, EvidenceRead, FindingRead

router = APIRouter(prefix="/cases/{case_id}", tags=["findings"])


@router.get("/findings", response_model=Page[FindingRead], summary="List a case's findings")
def list_findings(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    kind: FindingKind | None = Query(default=None),
    classification: Classification | None = Query(default=None),
    collector: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[FindingRead]:
    """Findings are already privacy-filtered: redacted values never leave here."""

    stmt = select(Finding).where(Finding.case_id == ctx.case_id)
    count_stmt = select(func.count()).select_from(Finding).where(Finding.case_id == ctx.case_id)
    for condition in (
        Finding.kind == kind if kind is not None else None,
        Finding.classification == classification if classification is not None else None,
        Finding.collector == collector if collector else None,
        Finding.confidence >= min_confidence if min_confidence > 0 else None,
    ):
        if condition is not None:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(
            stmt.order_by(Finding.confidence.desc(), Finding.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page[FindingRead](
        items=[FindingRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/evidence", response_model=Page[EvidenceRead], summary="List stored evidence")
def list_evidence(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    collector: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[EvidenceRead]:
    stmt = select(Evidence).where(Evidence.case_id == ctx.case_id)
    count_stmt = select(func.count()).select_from(Evidence).where(Evidence.case_id == ctx.case_id)
    if collector:
        stmt = stmt.where(Evidence.collector == collector)
        count_stmt = count_stmt.where(Evidence.collector == collector)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Evidence.retrieved_at.desc()).limit(limit).offset(offset))
    )
    return Page[EvidenceRead](
        items=[EvidenceRead.from_evidence(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/evidence/verify", summary="Verify stored evidence integrity")
def verify_evidence(
    session: DbSession,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
) -> dict:
    """Re-hash every stored artefact and report any that no longer match."""
    from app.services.evidence import EvidenceStore

    return EvidenceStore().verify_case(session, ctx.case_id)


@router.get("/runs", response_model=list[CollectorRunRead], summary="List collector runs")
def list_runs(
    session: DbSession,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
) -> list[CollectorRunRead]:
    """Every collector execution, including the ones that failed or were skipped."""
    runs = session.scalars(
        select(CollectorRun)
        .where(CollectorRun.case_id == ctx.case_id)
        .order_by(CollectorRun.created_at.desc())
    )
    return [CollectorRunRead.model_validate(run) for run in runs]
