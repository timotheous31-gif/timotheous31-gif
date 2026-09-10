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
from collections.abc import Sequence
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
from app.services.name_variants import (
    EXACT,
    EXTENDED,
    classify_observed_name,
    confidence_rule_for,
)
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

#: How a profile came to be in the case. A closed vocabulary, because a report
#: reader weighs "the investigator supplied this handle" differently from "a
#: name search returned it" — and because a free-text field would drift into
#: prose nobody can filter on.
DISCOVERY_SUPPLIED_ANCHOR = "supplied_anchor"
DISCOVERY_HANDLE_CHECK = "handle_check"
DISCOVERY_NAME_SEARCH = "name_search"
DISCOVERY_PUBLISHED_LINK = "published_link"
DISCOVERY_MANUAL_IMPORT = "manual_import"
DISCOVERY_PROVIDER_SEARCH = "provider_search"
DISCOVERY_API_RECORD = "api_record"

DISCOVERY_LABELS: dict[str, str] = {
    DISCOVERY_SUPPLIED_ANCHOR: "Supplied by the investigator as a known account",
    DISCOVERY_HANDLE_CHECK: "Public existence check for a supplied handle",
    DISCOVERY_NAME_SEARCH: "Returned by a public search for the name",
    DISCOVERY_PUBLISHED_LINK: "Linked from another public page the subject controls",
    DISCOVERY_MANUAL_IMPORT: "Imported by the investigator from a public search result",
    DISCOVERY_PROVIDER_SEARCH: "Returned by a configured public search provider",
    DISCOVERY_API_RECORD: "Read from a public API record",
}


def published_link_reason(origin: str) -> str:
    """The sentence recorded when one public page links to another.

    Regenerated from stored provenance on every refresh rather than kept in the
    reasons list, so it cannot survive as a stale claim and cannot be lost when
    the correlation is recomputed.
    """
    return (
        f"This profile is linked from {origin}, so that page's author published the "
        f"connection. Recorded as provenance; it does not raise the score, because a "
        f"published link is not proof of ownership."
    )


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
    handle_verified: bool = True,
    observed_affiliations: Sequence[str] = (),
    observed_locations: Sequence[str] = (),
) -> tuple[float, list[str], list[str], list[str]]:
    """Score a profile against the supplied anchors.

    Returns ``(confidence, match_reasons, mismatch_reasons, corroborated_by)``.
    The floor is the name-only rule, whose ceiling sits far below the auto-merge
    threshold, so no quantity of profiles can promote a candidate on its own.

    ``observed_affiliations`` and ``observed_locations`` are what the *source*
    published about this profile — an employer in a search snippet, an
    institution on an API record. They exist because the finding for a profile
    and the promoted profile itself must be scored on the same evidence: without
    them, a result whose page text matched a supplied employer scored 0.54 as a
    finding and 0.08 as a profile, and a report showed both. That is the same
    class of contradiction PR #9 fixed, arriving by a new route.

    ``handle_verified=False`` means nobody confirmed an account exists here:
    the URL was *built* from a handle the investigator supplied for a different
    platform. Its handle is then withheld from the anchor comparison, because
    matching a string against the string it was constructed from is not
    evidence — it is the same fact twice, and scoring it would let a lead about
    a stranger climb to the confidence of a confirmed account.
    """
    candidate = PersonCandidate(
        url=classified.url,
        name=display_name or subject_name,
        handles=[classified.handle] if (classified.handle and handle_verified) else [],
        affiliations=list(observed_affiliations),
        locations=list(observed_locations),
    )
    matched = anchor_matches(candidate, context)

    # The same variant-aware name rule the collectors use. Scoring a profile on
    # a flat "the name matched" while its own finding scored the spelling was
    # exactly how the two came to disagree once already.
    variant_type, variant_reason = classify_observed_name(candidate.name, subject_name)
    signals = [default_engine.signal(confidence_rule_for(variant_type))]
    corroborated: list[str] = []
    match_reasons: list[str] = []
    for kind, detail in matched:
        signals.append(default_engine.signal(ANCHOR_RULES[kind], detail=detail))
        corroborated.append(kind)
        match_reasons.append(ANCHOR_REASONS[kind].format(detail=detail))

    mismatch_reasons: list[str] = []
    if variant_type not in {EXACT, EXTENDED} and variant_reason:
        mismatch_reasons.append(variant_reason)
    if not corroborated:
        mismatch_reasons.append(
            "Nothing beyond the name connects this profile to the subject"
            + ("" if context.is_empty() else " — none of the anchors you supplied appears in it")
        )
    if context.is_empty():
        mismatch_reasons.append(
            "No anchors were supplied, so no profile here can be corroborated or ruled out"
        )
    if not handle_verified:
        mismatch_reasons.append(
            "Nobody confirmed that an account exists at this URL. It was built from a "
            "handle you supplied for another platform, and the same handle elsewhere "
            "may belong to somebody else entirely — open it yourself to check."
        )
    handle_anchored = {"username", "github_username"} & set(corroborated)
    if classified.handle and handle_verified and not handle_anchored:
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
    attributes: dict | None = None,
    linked_from: str | None = None,
    discovery_method: str | None = None,
    handle_verified: bool = True,
    observed_affiliations: Sequence[str] = (),
    observed_locations: Sequence[str] = (),
) -> SocialProfile | None:
    """Record a public profile URL, scored against the target's anchors.

    Returns ``None`` when the URL is not profile-shaped: a video page or a job
    listing is a web page, and storing it as a person's profile would invent an
    attribution out of a path.

    Calling this again for a URL already in the case **refreshes** it: the
    correlation is a pure function of the URL and the anchors currently on the
    target, so an older answer is simply wrong once the anchors change. Keeping
    one is how a report came to show a profile at 0.15 saying "no anchors were
    supplied" beside a finding at 0.70 saying the handle matched.

    ``linked_from`` records that another public page published this link. That
    is a real correlation signal and it is written down as a reason — but it
    deliberately fires no confidence rule. A person can link to an account that
    is not theirs, and the anchor model exists so that a score rises only on
    something the investigator supplied independently of the source.

    ``discovery_method`` is one of the ``DISCOVERY_*`` constants above.
    ``handle_verified=False`` marks a URL built from a supplied handle that
    nobody has checked — see :func:`assess_profile`.
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

    # Provenance merges; correlation is recomputed. Keeping them apart is what
    # lets a refresh replace a stale score without losing how the profile was
    # found, and lets a second pass that learned nothing new leave the first
    # pass's provenance intact.
    stored = dict((existing.attributes if existing is not None else None) or {})
    merged_attributes = {**stored, **(attributes or {})}
    if linked_from:
        merged_attributes["discovered_from"] = linked_from
    if discovery_method:
        merged_attributes["discovery_method"] = discovery_method

    confidence, match_reasons, mismatch_reasons, corroborated = assess_profile(
        classified,
        subject_name=subject_name,
        context=context,
        display_name=display_name,
        handle_verified=handle_verified,
        observed_affiliations=observed_affiliations,
        observed_locations=observed_locations,
    )
    origin = merged_attributes.get("discovered_from")
    if isinstance(origin, str) and origin:
        match_reasons.append(published_link_reason(origin))

    if existing is not None:
        # The correlation is *replaced*, not merged. It is a pure function of
        # the URL and the anchors currently on the target, so keeping an older
        # answer beside a newer one is how a report came to show a profile at
        # 0.15 saying "no anchors were supplied" next to a finding at 0.70
        # saying the handle matched. Both cannot be true of the same evidence,
        # and the stale one is always the wrong one to keep.
        existing.confidence = confidence
        existing.match_reasons = list(match_reasons)
        existing.mismatch_reasons = list(mismatch_reasons)
        existing.corroborated_by = list(corroborated)
        existing.accessibility = accessibility_for(classified)
        existing.server_fetchable = classified.server_fetchable
        existing.fetch_note = classified.fetch_note or None
        if candidate_entity_id and existing.candidate_entity_id is not candidate_entity_id:
            existing.candidate_entity_id = candidate_entity_id
        if target is not None and existing.target_id is None:
            existing.target_id = target.id
        if display_name and not existing.display_name:
            existing.display_name = display_name
        if bio and not existing.bio:
            existing.bio = bio
        if source_url and not existing.source_url:
            existing.source_url = source_url
        existing.attributes = merged_attributes
        existing.retrieved_at = moment
        session.flush()
        log.info(
            "social_profile.refreshed",
            case_id=str(case_id),
            platform=existing.platform,
            confidence=round(confidence, 4),
            corroborated_by=corroborated,
        )
        # Nothing here touches ``analyst_decisions``: the analyst's judgement
        # lives in its own table, so a refreshed automated score cannot reset
        # it and it cannot edit the score. That separation is the point of the
        # two tables, and it is asserted by a test rather than left to memory.
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
        attributes=merged_attributes,
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
