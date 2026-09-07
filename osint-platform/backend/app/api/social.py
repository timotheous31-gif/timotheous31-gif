"""Social profiles, public image evidence, candidate grouping and decisions."""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import CaseId, DbSession, parse_uuid
from app.core.errors import ValidationError
from app.models import Entity, EntityType, ImageEvidence, ImageFetchState, SocialProfile
from app.models.enums import DecisionSubject
from app.schemas.social import (
    AnalystDecisionRead,
    AnalystDecisionWrite,
    CandidateGroup,
    FetchImageRequest,
    ImageEvidenceRead,
    SocialProfileRead,
)
from app.services import decisions as decision_service
from app.services import images as image_service
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
    case_id: CaseId,
    session: DbSession,
    candidate_id: str | None = Query(default=None),
) -> list[SocialProfileRead]:
    entity_id = parse_uuid(candidate_id, "candidate_id") if candidate_id else None
    profiles = profile_service.profiles_for_case(session, case_id, candidate_entity_id=entity_id)
    decisions = decision_service.decision_map(session, case_id)
    return _decorate(profiles, decisions, SocialProfileRead)


@router.get("/images", response_model=list[ImageEvidenceRead], summary="Public image evidence")
def list_images(
    case_id: CaseId,
    session: DbSession,
    candidate_id: str | None = Query(default=None),
) -> list[ImageEvidenceRead]:
    entity_id = parse_uuid(candidate_id, "candidate_id") if candidate_id else None
    records = image_service.images_for_case(session, case_id, candidate_entity_id=entity_id)
    decisions = decision_service.decision_map(session, case_id)
    return _decorate(records, decisions, ImageEvidenceRead)


@router.post(
    "/images/{image_id}/fetch",
    response_model=ImageEvidenceRead,
    summary="Fetch a referenced image's bytes",
)
async def fetch_image(
    case_id: CaseId,
    image_id: str,
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
    if record is None or record.case_id != case_id:
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
def list_candidates(case_id: CaseId, session: DbSession) -> list[CandidateGroup]:
    """Every candidate with the profiles and images attributed to it.

    Grouped by candidate and by source. Never by visual similarity: the platform
    performs no image comparison of any kind, so it has no basis for that.
    """
    entities = list(
        session.scalars(
            select(Entity)
            .where(Entity.case_id == case_id, Entity.type == EntityType.PERSONA)
            .order_by(Entity.confidence.desc())
        )
    )
    candidates = [item for item in entities if item.attributes.get("role") == "candidate"]
    decisions = decision_service.decision_map(session, case_id)

    profiles = profile_service.profiles_for_case(session, case_id)
    images = image_service.images_for_case(session, case_id)

    groups: list[CandidateGroup] = []
    for entity in candidates:
        mine = [item for item in profiles if item.candidate_entity_id == entity.id]
        my_images = [item for item in images if item.candidate_entity_id == entity.id]
        found = decisions.get(str(entity.id))
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
                identity_established=bool(entity.attributes.get("identity_established", False)),
                social_profiles=_decorate(mine, decisions, SocialProfileRead),
                images=_decorate(my_images, decisions, ImageEvidenceRead),
                decision=AnalystDecisionRead.model_validate(found) if found else None,
            )
        )

    # Evidence nobody has attributed yet still has to be visible, or an
    # unattributed profile would silently vanish from the investigation.
    orphan_profiles = [item for item in profiles if item.candidate_entity_id is None]
    orphan_images = [item for item in images if item.candidate_entity_id is None]
    if orphan_profiles or orphan_images:
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
                social_profiles=_decorate(orphan_profiles, decisions, SocialProfileRead),
                images=_decorate(orphan_images, decisions, ImageEvidenceRead),
            )
        )
    return groups


@router.get("/decisions", response_model=list[AnalystDecisionRead], summary="Analyst decisions")
def list_decisions(case_id: CaseId, session: DbSession) -> list[AnalystDecisionRead]:
    return [
        AnalystDecisionRead.model_validate(record)
        for record in decision_service.decisions_for_case(session, case_id)
    ]


@router.post(
    "/decisions",
    response_model=AnalystDecisionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an analyst decision",
)
def record_decision(
    case_id: CaseId, payload: AnalystDecisionWrite, session: DbSession
) -> AnalystDecisionRead:
    """Record what the analyst concluded.

    Writes only to the decisions table. Automated confidence on the candidate,
    profile or image is left exactly as the platform computed it — the two are
    different claims and a report shows both.
    """
    record = decision_service.record_decision(
        session,
        case_id=case_id,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        decision=payload.decision,
        note=payload.note,
        decided_by=payload.decided_by,
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
    case_id: CaseId, subject_type: DecisionSubject, subject_id: str, session: DbSession
) -> None:
    """Withdraw a decision, leaving the automated assessment untouched.

    There is nothing to restore: recording a decision never overwrote it.
    """
    removed = decision_service.clear_decision(
        session,
        case_id=case_id,
        subject_type=subject_type,
        subject_id=parse_uuid(subject_id, "subject_id"),
    )
    if not removed:
        raise ValidationError("No decision recorded for that subject")
    session.commit()
