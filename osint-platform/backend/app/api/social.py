"""Social profiles, public image evidence, candidate grouping and decisions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import AppSettings, CaseContext, DbSession, parse_uuid, require
from app.core.errors import ValidationError
from app.core.permissions import Permission
from app.correlation import suppression
from app.models import Entity, EntityType, ImageEvidence, ImageFetchState, SocialProfile
from app.models.enums import AuditEvent, DecisionSubject
from app.schemas.social import (
    AnalystDecisionRead,
    AnalystDecisionWrite,
    CandidateGroup,
    FetchImageRequest,
    ImageEvidenceRead,
    PublicContactRead,
    SocialProfileRead,
)
from app.services import audit
from app.services import cases as case_service
from app.services import decisions as decision_service
from app.services import images as image_service
from app.services import promotion as contact_service
from app.services import social_profiles as profile_service

router = APIRouter(prefix="/cases/{case_id}", tags=["social"])


def _decorate(records, decisions, schema):
    """Attach each record's analyst decision without touching its confidence."""
    output = []
    for record in records:
        item = schema.model_validate(record)
        found = decisions.get(str(record.id))
        item.decision = AnalystDecisionRead.model_validate(found) if found else None
        output.append(item)
    return output


@router.get(
    "/social-profiles", response_model=list[SocialProfileRead], summary="Public social profiles"
)
def list_social_profiles(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    candidate_id: str | None = Query(default=None),
) -> list[SocialProfileRead]:
    entity_id = parse_uuid(candidate_id, "candidate_id") if candidate_id else None
    profiles = profile_service.profiles_for_case(
        session, ctx.case_id, candidate_entity_id=entity_id
    )
    decisions = decision_service.decision_map(session, ctx.case_id)
    return _decorate(profiles, decisions, SocialProfileRead)


@router.get(
    "/public-contacts",
    response_model=list[PublicContactRead],
    summary="Public professional and business contacts",
)
def list_public_contacts(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    candidate_id: str | None = Query(default=None),
) -> list[PublicContactRead]:
    """Public contacts, best-established provenance first.

    Only values a source published. Nothing here is derived from a name and a
    domain: a plausible guess printed beside real evidence reads as a finding,
    which is worse than returning nothing.
    """
    entity_id = parse_uuid(candidate_id, "candidate_id") if candidate_id else None
    contacts = contact_service.contacts_for_case(
        session, ctx.case_id, candidate_entity_id=entity_id
    )
    decisions = decision_service.decision_map(session, ctx.case_id)
    return _decorate(contacts, decisions, PublicContactRead)


@router.get("/images", response_model=list[ImageEvidenceRead], summary="Public image evidence")
def list_images(
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
    session: DbSession,
    candidate_id: str | None = Query(default=None),
) -> list[ImageEvidenceRead]:
    entity_id = parse_uuid(candidate_id, "candidate_id") if candidate_id else None
    records = image_service.images_for_case(session, ctx.case_id, candidate_entity_id=entity_id)
    decisions = decision_service.decision_map(session, ctx.case_id)
    return _decorate(records, decisions, ImageEvidenceRead)


@router.post(
    "/images/{image_id}/fetch",
    response_model=ImageEvidenceRead,
    summary="Fetch a referenced image's bytes",
)
async def fetch_image(
    image_id: str,
    ctx: Annotated[CaseContext, Depends(require(Permission.INVESTIGATION_RUN))],
    session: DbSession,
    payload: FetchImageRequest | None = None,
) -> ImageEvidenceRead:
    """Retrieve and hash an image that was previously recorded by reference.

    Goes through the same guarded HTTP path as every other fetch, so the SSRF
    rules apply to the URL and to every redirect. When the source platform is
    one that refuses anonymous server-side requests, this declines rather than
    trying: the block is recorded honestly and not worked around.
    """
    options = payload or FetchImageRequest()
    record = session.get(ImageEvidence, parse_uuid(image_id, "image_id"))
    if record is None or record.case_id != ctx.case_id:
        raise ValidationError(f"No image {image_id} in this case")

    if options.respect_platform_block and record.social_profile_id is not None:
        profile = session.get(SocialProfile, record.social_profile_id)
        if profile is not None and not profile.server_fetchable:
            record.fetch_note = (
                f"Not fetched. {profile.fetch_note or ''} " f"The image URL is kept as a reference."
            ).strip()
            record.fetch_state = ImageFetchState.REFERENCE_ONLY
            session.commit()
            session.refresh(record)
            return ImageEvidenceRead.model_validate(record)

    result = await image_service.fetch_image(record.image_url)
    record.fetch_state = result["fetch_state"]
    record.sha256 = result["sha256"] if result["fetch_state"] is ImageFetchState.FETCHED else None
    record.content_type = result["content_type"]
    record.byte_length = result["byte_length"]
    record.width = result["width"]
    record.height = result["height"]
    record.redirects = list(result["redirects"])
    record.final_url = result["final_url"]
    record.fetch_note = result["fetch_note"] or None
    session.commit()
    session.refresh(record)
    return ImageEvidenceRead.model_validate(record)


@router.get("/candidates", response_model=list[CandidateGroup], summary="Candidates and evidence")
def list_candidates(
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
) -> list[CandidateGroup]:
    """Every candidate with the profiles and images attributed to it.

    Grouped by candidate and by source. Never by visual similarity: the platform
    performs no image comparison of any kind, so it has no basis for that.
    """
    entities = list(
        session.scalars(
            select(Entity)
            .where(Entity.case_id == ctx.case_id, Entity.type == EntityType.PERSONA)
            .order_by(Entity.confidence.desc())
        )
    )
    candidates = [item for item in entities if item.attributes.get("role") == "candidate"]
    decisions = decision_service.decision_map(session, ctx.case_id)
    # Presentation is computed per request, never stored: changing the threshold
    # changes what the next response groups where, and rewrites nothing.
    threshold = settings.candidate_suppression_threshold
    supplied = case_service.anchors_supplied(session, ctx.case_id)

    profiles = profile_service.profiles_for_case(session, ctx.case_id)
    images = image_service.images_for_case(session, ctx.case_id)
    contacts = contact_service.contacts_for_case(session, ctx.case_id)

    groups: list[CandidateGroup] = []
    for entity in candidates:
        mine = [item for item in profiles if item.candidate_entity_id == entity.id]
        my_images = [item for item in images if item.candidate_entity_id == entity.id]
        my_contacts = [item for item in contacts if item.candidate_entity_id == entity.id]
        found = decisions.get(str(entity.id))
        placement = suppression.classify_candidate(
            score=entity.confidence,
            corroborated_by=list(entity.attributes.get("corroborated_by") or []),
            decision=str(found.decision) if found else None,
            threshold=threshold,
            anchors_supplied=supplied,
        )
        groups.append(
            CandidateGroup(
                entity_id=entity.id,
                display_name=entity.display_name,
                canonical_value=entity.canonical_value,
                confidence=entity.confidence,
                confidence_reasons=list(entity.confidence_reasons or []),
                match_reasons=list(entity.attributes.get("match_reasons") or []),
                mismatch_reasons=list(entity.attributes.get("mismatch_reasons") or []),
                corroborated_by=list(entity.attributes.get("corroborated_by") or []),
                presentation=placement.presentation,
                presentation_reason=placement.reason,
                identity_established=bool(entity.attributes.get("identity_established", False)),
                social_profiles=_decorate(mine, decisions, SocialProfileRead),
                images=_decorate(my_images, decisions, ImageEvidenceRead),
                public_contacts=_decorate(my_contacts, decisions, PublicContactRead),
                decision=AnalystDecisionRead.model_validate(found) if found else None,
            )
        )

    # Evidence nobody has attributed yet still has to be visible, or an
    # unattributed profile would silently vanish from the investigation.
    orphan_profiles = [item for item in profiles if item.candidate_entity_id is None]
    orphan_images = [item for item in images if item.candidate_entity_id is None]
    orphan_contacts = [item for item in contacts if item.candidate_entity_id is None]
    if orphan_profiles or orphan_images or orphan_contacts:
        groups.append(
            CandidateGroup(
                entity_id=None,
                display_name="Unattributed evidence",
                canonical_value="unattributed",
                confidence=0.0,
                confidence_reasons=[
                    "Not attributed to a candidate. Imported evidence appears here "
                    "until an analyst assigns it."
                ],
                match_reasons=[],
                mismatch_reasons=[],
                corroborated_by=[],
                # Always primary. This group is evidence nobody has attributed
                # yet, not a weak match — folding it away is exactly how an
                # imported profile would silently vanish.
                presentation=suppression.PRIMARY,
                presentation_reason=(
                    "Evidence waiting to be attributed to a candidate. Shown so it "
                    "cannot be lost, whatever any candidate scores."
                ),
                social_profiles=_decorate(orphan_profiles, decisions, SocialProfileRead),
                images=_decorate(orphan_images, decisions, ImageEvidenceRead),
                public_contacts=_decorate(orphan_contacts, decisions, PublicContactRead),
            )
        )
    return groups


@router.get("/decisions", response_model=list[AnalystDecisionRead], summary="Analyst decisions")
def list_decisions(
    session: DbSession,
    ctx: Annotated[CaseContext, Depends(require(Permission.CASE_READ))],
) -> list[AnalystDecisionRead]:
    return [
        AnalystDecisionRead.model_validate(record)
        for record in decision_service.decisions_for_case(session, ctx.case_id)
    ]


@router.post(
    "/decisions",
    response_model=AnalystDecisionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an analyst decision",
)
def record_decision(
    payload: AnalystDecisionWrite,
    ctx: Annotated[CaseContext, Depends(require(Permission.ANALYST_DECIDE))],
    session: DbSession,
) -> AnalystDecisionRead:
    """Record what the analyst concluded.

    Writes only to the decisions table. Automated confidence on the candidate,
    profile or image is left exactly as the platform computed it — the two are
    different claims and a report shows both.
    """
    record = decision_service.record_decision(
        session,
        case_id=ctx.case_id,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        decision=payload.decision,
        note=payload.note,
        decided_by=payload.decided_by,
    )
    audit.record(
        session,
        event=AuditEvent.ANALYST_DECISION_CREATED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type=str(payload.subject_type),
        object_id=payload.subject_id,
        metadata={
            "case_id": str(ctx.case_id),
            "decision": str(payload.decision),
            # The note is the analyst's own words about a person under
            # investigation. It stays on the decision record, where it belongs;
            # copying it into the security log would spread it for no benefit.
            "has_note": bool(payload.note),
        },
    )
    session.commit()
    session.refresh(record)
    return AnalystDecisionRead.model_validate(record)


@router.delete(
    "/decisions/{subject_type}/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw an analyst decision",
)
def clear_decision(
    subject_type: DecisionSubject,
    subject_id: str,
    ctx: Annotated[CaseContext, Depends(require(Permission.ANALYST_DECIDE))],
    session: DbSession,
) -> None:
    """Withdraw a decision, leaving the automated assessment untouched.

    There is nothing to restore: recording a decision never overwrote it.
    """
    removed = decision_service.clear_decision(
        session,
        case_id=ctx.case_id,
        subject_type=subject_type,
        subject_id=parse_uuid(subject_id, "subject_id"),
    )
    if not removed:
        raise ValidationError("No decision recorded for that subject")
    audit.record(
        session,
        event=AuditEvent.ANALYST_DECISION_WITHDRAWN,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="decision",
        object_id=subject_id,
        metadata={"case_id": str(ctx.case_id), "subject_type": str(subject_type)},
    )
    session.commit()
