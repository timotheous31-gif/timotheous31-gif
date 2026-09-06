"""Reconnaissance endpoints: generated queries and investigator-imported results."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CaseId, DbSession, parse_uuid
from app.collectors.person import PersonContext
from app.core.errors import NotFoundError, ValidationError
from app.models import Finding, Target
from app.models.enums import FindingKind, TargetType
from app.schemas.recon import (
    ImportedResultRead,
    ManualResultImport,
    ReconQueryPlan,
    ReconQueryRead,
)
from app.services import recon_import
from app.services.normalization import NormalizedTarget
from app.services.recon import generate_queries

router = APIRouter(prefix="/cases/{case_id}", tags=["recon"])


def _person_target(session: DbSession, case_id: CaseId, target_id: str) -> Target:
    target = session.get(Target, parse_uuid(target_id, "target_id"))
    if target is None or target.case_id != case_id:
        raise NotFoundError(f"Target {target_id} is not in case {case_id}")
    if target.type is not TargetType.PERSON:
        raise ValidationError(
            "Reconnaissance queries are generated for PERSON targets only",
            detail={"target_type": str(target.type)},
        )
    return target


@router.get(
    "/targets/{target_id}/recon-queries",
    response_model=ReconQueryPlan,
    summary="Generated reconnaissance queries for a PERSON target",
)
def recon_queries(case_id: CaseId, target_id: str, session: DbSession) -> ReconQueryPlan:
    """The searches to run by hand, with the reason for each.

    The platform never submits these anywhere. It generates them, the
    investigator runs them in their own browser, and relevant public results
    come back through the import endpoint.
    """
    target = _person_target(session, case_id, target_id)
    normalized = NormalizedTarget(
        type=target.type,
        raw_input=target.raw_input,
        value=target.normalized_value,
        attributes=dict(target.attributes or {}),
    )
    context = PersonContext.from_target(normalized)
    name = str(normalized.attributes.get("display_name", target.normalized_value))

    return ReconQueryPlan(
        target_id=target.id,
        subject_name=name,
        queries=[ReconQueryRead(**query.as_dict()) for query in generate_queries(name, context)],
        anchors_used=context.describe(),
    )


@router.post(
    "/targets/{target_id}/recon-results",
    response_model=list[ImportedResultRead],
    status_code=status.HTTP_201_CREATED,
    summary="Import public search results found by the investigator",
)
def import_recon_results(
    case_id: CaseId, target_id: str, payload: ManualResultImport, session: DbSession
) -> list[ImportedResultRead]:
    """Store results the investigator selected from their own search.

    Every URL passes the platform's SSRF guard before it is stored, and each
    import is hashed into the evidence store with the query and engine that
    produced it. Imported results are filed under their own collector identity
    so nothing later mistakes them for something a search API returned.
    """
    target = _person_target(session, case_id, target_id)
    findings = recon_import.import_results(session, case_id, target.id, payload)
    session.commit()
    return [_read(finding) for finding in findings]


@router.get(
    "/recon-results",
    response_model=list[ImportedResultRead],
    summary="List investigator-imported results and image evidence",
)
def list_recon_results(
    case_id: CaseId, session: DbSession, images_only: bool = False
) -> list[ImportedResultRead]:
    kinds = (
        [FindingKind.IMAGE_EVIDENCE]
        if images_only
        else [FindingKind.MANUAL_SEARCH_RESULT, FindingKind.IMAGE_EVIDENCE]
    )
    findings = session.scalars(
        select(Finding)
        .where(Finding.case_id == case_id, Finding.kind.in_(kinds))
        .order_by(Finding.created_at.desc())
    ).all()
    return [_read(finding) for finding in findings]


def _read(finding: Finding) -> ImportedResultRead:
    data = dict(finding.data or {})
    return ImportedResultRead(
        id=finding.id,
        url=str(data.get("url", "")),
        title=finding.title,
        snippet=str(data.get("snippet") or ""),
        query=str(data.get("query", "")),
        engine=str(data.get("engine", "")),
        platform=data.get("platform"),
        platform_label=data.get("platform_label"),
        url_kind=data.get("url_kind"),
        handle=data.get("handle"),
        is_image=finding.kind is FindingKind.IMAGE_EVIDENCE,
        image_url=data.get("image_url"),
        thumbnail_url=data.get("thumbnail_url"),
        caption=data.get("caption"),
        evidence_class=str(data.get("evidence_class", "")),
        imported_at=finding.observed_at,
        confidence=finding.confidence,
        evidence_sha256=[item.sha256 for item in (finding.evidence or [])],
    )
