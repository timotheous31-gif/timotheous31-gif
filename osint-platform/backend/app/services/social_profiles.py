"""Persisting public social profiles, and tying them to candidates.

The correlation here is deliberately the *same* correlation the collectors use:
:func:`app.collectors.person.anchor_matches` against the anchors on the target.
Whether an anchor matches is a property of the record and the subject, not of
how the record was found — and a parallel implementation for the manual path is
exactly how the two came to disagree once already.

A matching username is never, on its own, evidence that an account belongs to
the subject. Handles are reused, sold and coincidental. So a profile whose only
connection is a name or a lookalike handle stays where the name-only rule puts
it, well below anything that could merge identities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.person import PersonCandidate, PersonContext, anchor_matches
from app.collectors.social import SocialProfile as ClassifiedUrl
from app.collectors.social import classify_url
from app.core.logging import get_logger
from app.correlation.anchors import ANCHOR_REASONS, ANCHOR_RULES
from app.correlation.confidence import default_engine
from app.models import ProfileAccess, SocialProfile, Target
from app.models.enums import TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)


def target_context(target: Target) -> PersonContext:
    """The anchors stored on a PERSON target, read the way collectors read them."""
    return PersonContext.from_target(
        NormalizedTarget(
            type=target.type,
            raw_input=target.raw_input,
            value=target.normalized_value,
            attributes=dict(target.attributes or {}),
        )
    )


def accessibility_for(classified: ClassifiedUrl) -> ProfileAccess:
    """What we can honestly say about reaching this profile.

    RESTRICTED is a statement about the platform's policy towards anonymous
    server-side clients, recorded so a report can explain why nothing was
    fetched. It is never a reason to try something else.
    """
    if not classified.server_fetchable:
        return ProfileAccess.RESTRICTED
    return ProfileAccess.UNKNOWN


def assess_profile(
    classified: ClassifiedUrl,
    *,
    subject_name: str,
    context: PersonContext,
    display_name: str | None = None,
) -> tuple[float, list[str], list[str], list[str]]:
    """Score a profile against the supplied anchors.

    Returns ``(confidence, match_reasons, mismatch_reasons, corroborated_by)``.
    The floor is the name-only rule, whose ceiling sits far below the auto-merge
    threshold, so no quantity of profiles can promote a candidate on its own.
    """
    candidate = PersonCandidate(
        url=classified.url,
        name=display_name or subject_name,
        handles=[classified.handle] if classified.handle else [],
    )
    matched = anchor_matches(candidate, context)

    signals = [default_engine.signal("same_person_name")]
    corroborated: list[str] = []
    match_reasons: list[str] = []
    for kind, detail in matched:
        signals.append(default_engine.signal(ANCHOR_RULES[kind], detail=detail))
        corroborated.append(kind)
        match_reasons.append(ANCHOR_REASONS[kind].format(detail=detail))

    mismatch_reasons: list[str] = []
    if not corroborated:
        mismatch_reasons.append(
            "Nothing beyond the name connects this profile to the subject"
            + ("" if context.is_empty() else " — none of the anchors you supplied appears in it")
        )
    if context.is_empty():
        mismatch_reasons.append(
            "No anchors were supplied, so no profile here can be corroborated or ruled out"
        )
    handle_anchored = {"username", "github_username"} & set(corroborated)
    if classified.handle and not handle_anchored:
        mismatch_reasons.append(
            f"The handle {classified.handle!r} is not one you supplied; a matching "
            f"handle alone would not establish ownership in any case"
        )
    if not classified.server_fetchable:
        mismatch_reasons.append(
            "This platform refuses anonymous server-side requests, so only the URL "
            "shape was read — the page content has not been checked"
        )

    return (default_engine.score(signals).score, match_reasons, mismatch_reasons, corroborated)


def record_profile(
    session: Session,
    *,
    case_id: uuid.UUID,
    target: Target | None,
    url: str,
    collector: str,
    evidence_class: str,
    display_name: str | None = None,
    bio: str | None = None,
    source_url: str | None = None,
    candidate_entity_id: uuid.UUID | None = None,
    retrieved_at: datetime | None = None,
) -> SocialProfile | None:
    """Record a public profile URL, scored against the target's anchors.

    Returns ``None`` when the URL is not profile-shaped: a video page or a job
    listing is a web page, and storing it as a person's profile would invent an
    attribution out of a path.
    """
    classified = classify_url(url)
    if classified is None or not classified.is_social:
        return None

    moment = retrieved_at or datetime.now(UTC)
    existing = session.scalar(
        select(SocialProfile).where(
            SocialProfile.case_id == case_id,
            SocialProfile.profile_url == classified.url,
        )
    )

    subject_name = ""
    context = PersonContext()
    if target is not None and target.type is TargetType.PERSON:
        subject_name = str(target.attributes.get("display_name", target.normalized_value))
        context = target_context(target)

    confidence, match_reasons, mismatch_reasons, corroborated = assess_profile(
        classified, subject_name=subject_name, context=context, display_name=display_name
    )

    if existing is not None:
        if candidate_entity_id and existing.candidate_entity_id is None:
            existing.candidate_entity_id = candidate_entity_id
        if display_name and not existing.display_name:
            existing.display_name = display_name
        if bio and not existing.bio:
            existing.bio = bio
        session.flush()
        return existing

    profile = SocialProfile(
        case_id=case_id,
        candidate_entity_id=candidate_entity_id,
        target_id=target.id if target is not None else None,
        platform=classified.platform,
        platform_label=classified.display_name,
        handle=classified.handle,
        profile_url=classified.url,
        display_name=display_name,
        bio=bio,
        source_url=source_url,
        accessibility=accessibility_for(classified),
        server_fetchable=classified.server_fetchable,
        fetch_note=classified.fetch_note or None,
        collector=collector,
        evidence_class=evidence_class,
        confidence=confidence,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        corroborated_by=corroborated,
        retrieved_at=moment,
        attributes={},
    )
    session.add(profile)
    session.flush()
    log.info(
        "social_profile.recorded",
        case_id=str(case_id),
        platform=profile.platform,
        corroborated_by=corroborated,
        server_fetchable=profile.server_fetchable,
    )
    return profile


def profiles_for_case(
    session: Session, case_id: uuid.UUID, *, candidate_entity_id: uuid.UUID | None = None
) -> list[SocialProfile]:
    """Every social profile in the case, strongest first."""
    query = select(SocialProfile).where(SocialProfile.case_id == case_id)
    if candidate_entity_id is not None:
        query = query.where(SocialProfile.candidate_entity_id == candidate_entity_id)
    return list(session.scalars(query.order_by(SocialProfile.confidence.desc())))
