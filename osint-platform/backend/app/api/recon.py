"""Reconnaissance endpoints: generated queries and investigator-imported results."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CaseContext,
    DbSession,
    parse_uuid,
    require,
)
from app.collectors.capabilities import CAPABILITIES
from app.collectors.person import PersonContext
from app.core import throttle
from app.core.errors import NotFoundError, ThrottledError, ValidationError
from app.core.permissions import Permission
from app.models import Finding, SocialProfile, Target
from app.models.enums import AuditEvent, FindingKind, TargetType
from app.schemas.recon import (
    DiscoveredAnchorRead,
    ImportedResultRead,
    ManualResultImport,
    NameVariantRead,
    ReconQueryPlan,
    ReconQueryRead,
    ReconStageRead,
    SearchIngestRead,
    SourcePlatformRead,
    StagedReconPlan,
)
from app.services import audit, recon_import
from app.services.normalization import NormalizedTarget
from app.services.promotion import promote_finding
from app.services.providers.search import get_search_provider
from app.services.recon import discovered_anchors, generate_queries, staged_plan
from app.services.search_ingest import search_target

router = APIRouter(prefix="/cases/{case_id}", tags=["recon"])


def _person_target(session: DbSession, case_id: uuid.UUID, target_id: str) -> Target:
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
def recon_queries(
    target_id: str,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
) -> ReconQueryPlan:
    """The searches to run by hand, with the reason for each.

    The platform never submits these anywhere. It generates them, the
    investigator runs them in their own browser, and relevant public results
    come back through the import endpoint.
    """
    target = _person_target(session, ctx.case_id, target_id)
    normalized = NormalizedTarget(
        type=target.type,
        raw_input=target.raw_input,
        value=target.normalized_value,
        attributes=dict(target.attributes or {}),
    )
    context = PersonContext.from_target(normalized)
    name = str(normalized.attributes.get("display_name", target.normalized_value))

    # Discovery feeds recon: a fuller name a public profile declared is worth
    # searching, and the investigator should not have to retype it. The target's
    # own name is untouched — these are extra searches, not a rename.
    declared = _declared_names(session, ctx.case_id, name)
    queries = generate_queries(name, context, also_known_as=declared)

    return ReconQueryPlan(
        target_id=target.id,
        subject_name=name,
        queries=[ReconQueryRead(**query.as_dict()) for query in queries],
        anchors_used=context.describe(),
        also_known_as=list(declared),
        capabilities=[_capability(item) for item in CAPABILITIES],
    )


def _capability(item: Any) -> SourcePlatformRead:
    return SourcePlatformRead(
        platform=item.platform,
        display_name=item.display_name,
        domains=list(item.domains),
        server_fetchable=item.server_fetchable,
        public_api_available=item.public_api_available,
        manual_search_supported=item.manual_search_supported,
        handle_check_supported=item.handle_check_supported,
        image_reference_supported=item.image_reference_supported,
        search_filters=list(item.search_filters),
        notes=item.notes or item.fetch_note or None,
    )


def _declared_names(session: DbSession, case_id: uuid.UUID, searched: str) -> tuple[str, ...]:
    """Fuller name spellings public profiles in this case declared.

    Only names a source actually published, and only where the searched name is
    contained in them: an unrelated name on an unrelated profile is not a
    variant of the subject's, and searching it would be searching for somebody
    else.
    """
    wanted = {token for token in searched.lower().split() if token}
    found: list[str] = []
    for profile in session.scalars(select(SocialProfile).where(SocialProfile.case_id == case_id)):
        declared = (profile.declared_name or "").strip()
        if not declared or declared.lower() == searched.lower():
            continue
        parts = {token for token in declared.lower().split() if token}
        if wanted and wanted <= parts and declared not in found:
            found.append(declared)
    return tuple(found)


@router.get(
    "/targets/{target_id}/recon-plan",
    response_model=StagedReconPlan,
    summary="The staged reconnaissance plan for a PERSON target",
)
def recon_plan(
    target_id: str,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
) -> StagedReconPlan:
    """The plan an investigator works through, stage by stage.

    Built from the name's variants, the anchors supplied, and the anchors public
    sources actually published about candidates in this case. Nothing is
    invented: a discovered anchor carries the URL that published it.
    """
    target = _person_target(session, ctx.case_id, target_id)
    normalized = NormalizedTarget(
        type=target.type,
        raw_input=target.raw_input,
        value=target.normalized_value,
        attributes=dict(target.attributes or {}),
    )
    context = PersonContext.from_target(normalized)
    canonical = str(normalized.attributes.get("display_name", target.normalized_value))
    declared = _declared_names(session, ctx.case_id, canonical)
    plan = staged_plan(
        canonical,
        context,
        discovered=discovered_anchors(session, ctx.case_id),
        also_known_as=declared,
    )

    provider = get_search_provider()
    configured, note = provider.is_available()
    payload = plan.as_dict()
    return StagedReconPlan(
        target_id=target.id,
        canonical=payload["canonical"],
        variants=[NameVariantRead(**item) for item in payload["variants"]],
        stages=[ReconStageRead(**item) for item in payload["stages"]],
        discovered_anchors=[DiscoveredAnchorRead(**item) for item in payload["discovered_anchors"]],
        anchors_used=context.describe(),
        also_known_as=list(declared),
        capabilities=[_capability(item) for item in CAPABILITIES],
        search_provider=provider.key,
        search_provider_configured=configured,
        search_provider_note=note,
    )


@router.post(
    "/targets/{target_id}/search",
    response_model=SearchIngestRead,
    summary="Run the plan through a configured search provider and ingest the results",
)
async def run_provider_search(
    target_id: str,
    ctx: Annotated[CaseContext, Depends(require(Permission.INVESTIGATION_RUN))],
    session: DbSession,
    settings: AppSettings,
) -> SearchIngestRead:
    """Search the public web and feed what returns into the investigation.

    With no provider configured this does nothing and says so — which is the
    point. A report has to distinguish "the public web returned nothing" from
    "the public web was never searched", and only the caller can fix the second.

    Results reach the report through the same pipeline a collector's findings
    use: no special confidence for having been returned by a search engine.
    """
    verdict = throttle.check(
        throttle.principal_key("recon-search", str(ctx.principal.user_id)),
        limit=settings.rate_limit_recon_per_hour,
        window_seconds=3600,
        settings=settings,
    )
    if verdict.refused:
        # Explicitly *not* an empty result set. A throttled search is a search
        # that did not happen, and the coverage model has a state for that.
        raise ThrottledError(
            "You have run a lot of provider searches in a short time. This channel "
            "is billed per search, so there is an hourly ceiling. Nothing was "
            "searched — this is not a result.",
            retry_after=verdict.retry_after,
        )

    target = _person_target(session, ctx.case_id, target_id)
    report = await search_target(session, case_id=ctx.case_id, target_id=target.id)
    for finding_id in report.findings:
        finding = session.get(Finding, finding_id)
        if finding is None:
            continue
        promote_finding(
            session,
            case_id=ctx.case_id,
            finding=finding,
            target=target,
            candidate_entity_id=None,
        )
    session.commit()
    payload = report.to_dict()
    return SearchIngestRead(**payload)


@router.post(
    "/targets/{target_id}/recon-results",
    response_model=list[ImportedResultRead],
    status_code=status.HTTP_201_CREATED,
    summary="Import public search results found by the investigator",
)
def import_recon_results(
    target_id: str,
    payload: ManualResultImport,
    ctx: Annotated[CaseContext, Depends(require(Permission.RESULT_IMPORT))],
    session: DbSession,
    settings: AppSettings,
) -> list[ImportedResultRead]:
    """Store results the investigator selected from their own search.

    Every URL passes the platform's SSRF guard before it is stored, and each
    import is hashed into the evidence store with the query and engine that
    produced it. Imported results are filed under their own collector identity
    so nothing later mistakes them for something a search API returned.
    """
    verdict = throttle.check(
        throttle.principal_key("recon-import", str(ctx.principal.user_id)),
        limit=settings.rate_limit_import_per_hour,
        window_seconds=3600,
        settings=settings,
    )
    if verdict.refused:
        raise ThrottledError(
            "You have imported a lot of results in a short time. Try again shortly.",
            retry_after=verdict.retry_after,
        )

    target = _person_target(session, ctx.case_id, target_id)
    findings = recon_import.import_results(session, ctx.case_id, target.id, payload)
    audit.record(
        session,
        event=AuditEvent.MANUAL_RESULT_IMPORTED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="target",
        object_id=target.id,
        metadata={"case_id": str(ctx.case_id), "results": len(findings)},
    )
    session.commit()
    return [_read(finding) for finding in findings]


@router.get(
    "/recon-results",
    response_model=list[ImportedResultRead],
    summary="List investigator-imported results and image evidence",
)
def list_recon_results(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    images_only: bool = False,
) -> list[ImportedResultRead]:
    kinds = (
        [FindingKind.IMAGE_EVIDENCE]
        if images_only
        else [FindingKind.MANUAL_SEARCH_RESULT, FindingKind.IMAGE_EVIDENCE]
    )
    findings = session.scalars(
        select(Finding)
        .where(Finding.case_id == ctx.case_id, Finding.kind.in_(kinds))
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
