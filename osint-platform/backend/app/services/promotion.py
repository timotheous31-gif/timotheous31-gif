"""Promoting collector output into structured, reviewable evidence.

The problem this solves: a GitHub account discovered automatically used to
produce strictly less than the same URL pasted in by hand. The manual import
path created a SocialProfile, correlated it against the anchors and recorded
image evidence; the collector path left all of that inside a JSON blob for the
investigator to find. That is backwards, and it is why a finished report could
list a GitHub candidate without ever mentioning the account's avatar, its
stated company, or the profile as a profile.

So promotion runs over *persisted findings* and reuses the services PR #7
already built — ``record_profile``, ``record_image`` — rather than a second
implementation. Nothing here fetches anything or invents a fact: it moves public
values the collector already retrieved into the structures that make them
visible, correlated and reviewable.

Two rules govern every function below:

* **Never derive a contact.** An address is promoted only when a source
  published it. ``first.last@employer.com`` is a guess, and a guess printed
  beside real evidence reads as a finding.
* **Never claim identity from an image.** An avatar is evidence that an account
  publishes a picture. It says nothing about who is in it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.social import classify_url
from app.core.logging import get_logger
from app.models import (
    ContactClassification,
    ContactType,
    Entity,
    Finding,
    ImageFetchState,
    PublicContact,
    SocialProfile,
    Target,
)
from app.models.enums import FindingKind
from app.services.evidence import EvidenceStore
from app.services.images import record_image
from app.services.social_profiles import record_profile

log = get_logger(__name__)

#: Collectors whose records are published by the person or a registry, so a
#: contact they expose is self-published or professional rather than a passing
#: mention. Anything not listed stays UNVERIFIED_PUBLIC_REFERENCE.
SELF_PUBLISHED_SOURCES = frozenset({"github_people", "github", "person_usernames"})
PROFESSIONAL_SOURCES = frozenset({"orcid", "openalex", "crossref", "wikidata"})

#: Keys a collector may use for a public email it actually received. Read in
#: order; the first present wins. No key here is ever *constructed*.
EMAIL_KEYS = ("public_email", "email")
#: Keys carrying a personal or professional website the source published.
WEBSITE_KEYS = ("blog", "website", "homepage", "url_homepage", "repository_homepage")
#: Keys carrying a profile picture the source published.
IMAGE_KEYS = ("avatar_url", "profile_image_url", "image_url", "thumbnail_url")

#: Fact kinds from a profile README that describe a person professionally.
#: Shown as a block on the profile rather than promoted into contacts: an
#: occupation is not something you can write to.
DESCRIPTIVE_KINDS = ("declared_name", "occupation", "employer", "professional_field", "location")


def classification_for(source: str) -> ContactClassification:
    """How well established a contact from ``source`` is.

    About provenance, never about how official the value looks: an address on a
    person's own profile is self-published however plausible its domain.
    """
    if source in SELF_PUBLISHED_SOURCES:
        return ContactClassification.PUBLIC_SELF_PUBLISHED
    if source in PROFESSIONAL_SOURCES:
        return ContactClassification.PUBLIC_PROFESSIONAL
    return ContactClassification.UNVERIFIED_PUBLIC_REFERENCE


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    """The first non-empty value among ``keys``, searching ``extra`` as well."""
    raw_extra = data.get("extra")
    extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
    for key in keys:
        for source in (data, extra):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def record_contact(
    session: Session,
    *,
    case_id: uuid.UUID,
    contact_type: ContactType,
    value: str,
    source_name: str,
    collector: str,
    evidence_class: str,
    extraction_reason: str,
    classification: ContactClassification | None = None,
    source_url: str | None = None,
    label: str | None = None,
    candidate_entity_id: uuid.UUID | None = None,
    social_profile_id: uuid.UUID | None = None,
    evidence_id: uuid.UUID | None = None,
    confidence: float = 0.0,
    confidence_reasons: list[str] | None = None,
    retrieved_at: datetime | None = None,
) -> PublicContact | None:
    """Store one public contact, deduplicating on (type, value) within the case.

    The same address published by two sources is one contact corroborated
    twice, not two contacts — so a repeat records the extra source rather than
    inserting a row, and never counts as independent agreement with itself.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return None

    existing = session.scalar(
        select(PublicContact).where(
            PublicContact.case_id == case_id,
            PublicContact.contact_type == contact_type,
            PublicContact.value == cleaned,
        )
    )
    if existing is not None:
        seen = list(existing.attributes.get("also_seen_in") or [])
        if source_name != existing.source_name and source_name not in seen:
            seen.append(source_name)
            existing.attributes = {**existing.attributes, "also_seen_in": seen}
        if candidate_entity_id and existing.candidate_entity_id is None:
            existing.candidate_entity_id = candidate_entity_id
        session.flush()
        return existing

    contact = PublicContact(
        case_id=case_id,
        candidate_entity_id=candidate_entity_id,
        social_profile_id=social_profile_id,
        contact_type=contact_type,
        value=cleaned,
        label=label,
        classification=classification or classification_for(collector),
        source_url=source_url,
        source_name=source_name,
        collector=collector,
        evidence_class=evidence_class,
        evidence_id=evidence_id,
        confidence=confidence,
        confidence_reasons=list(confidence_reasons or []),
        extraction_reason=extraction_reason,
        retrieved_at=retrieved_at or datetime.now(UTC),
        attributes={},
    )
    session.add(contact)
    session.flush()
    log.info(
        "contact.promoted",
        case_id=str(case_id),
        contact_type=str(contact_type),
        classification=str(contact.classification),
        source=source_name,
    )
    return contact


def promote_finding(
    session: Session,
    *,
    case_id: uuid.UUID,
    finding: Finding,
    target: Target | None,
    candidate_entity_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Promote one persisted PERSON_CANDIDATE finding into structured evidence.

    Returns counters so a run can report what it surfaced. Every promotion is
    idempotent: the underlying services deduplicate, so re-running an
    investigation strengthens the record rather than multiplying it.
    """
    counts = {"profiles": 0, "images": 0, "contacts": 0}
    if finding.kind is not FindingKind.PERSON_CANDIDATE:
        return counts

    data = finding.data or {}
    url = str(data.get("url") or "")
    source = str(data.get("source") or finding.collector)
    source_label = str(data.get("source_label") or source)
    evidence_class = "api_fetched"
    moment = finding.observed_at or datetime.now(UTC)
    if not url:
        return counts

    # 1. The candidate's own page, when it is profile-shaped. record_profile
    #    returns None for anything else, so a publication DOI stays a document.
    profile = record_profile(
        session,
        case_id=case_id,
        target=target,
        url=url,
        collector=source,
        evidence_class=evidence_class,
        display_name=str(data.get("candidate_name") or "") or None,
        bio=_first(data, ("bio",)) or None,
        source_url=url,
        candidate_entity_id=candidate_entity_id,
        retrieved_at=moment,
        attributes=profile_attributes(data),
    )
    if profile is not None:
        counts["profiles"] += 1

    # 2. A profile picture the source published. Recorded by reference: the
    #    collector did not download these bytes, and claiming a hash for bytes
    #    nobody read would be a fabricated guarantee. A later fetch upgrades it.
    image_url = _first(data, IMAGE_KEYS)
    if image_url:
        record_image(
            session,
            case_id=case_id,
            image_url=image_url,
            source_page_url=url,
            origin=source,
            evidence_class=evidence_class,
            platform=(profile.platform if profile else None),
            caption=f"Profile picture published on {source_label}",
            candidate_entity_id=candidate_entity_id,
            social_profile_id=profile.id if profile else None,
            fetch={
                "fetch_state": ImageFetchState.REFERENCE_ONLY,
                "fetch_note": (
                    f"{source_label} published this image URL on the profile. The "
                    f"bytes were not downloaded during collection; fetch the image "
                    f"to hash it."
                ),
                "sha256": None,
                "content_type": None,
                "byte_length": None,
                "width": None,
                "height": None,
                "redirects": [],
                "final_url": None,
            },
            retrieved_at=moment,
        )
        counts["images"] += 1

    # 3. Contacts the source *published*. Nothing is derived.
    email = _first(data, EMAIL_KEYS)
    if email and "@" in email:
        record_contact(
            session,
            case_id=case_id,
            contact_type=ContactType.EMAIL,
            value=email,
            source_name=source_label,
            collector=source,
            evidence_class=evidence_class,
            source_url=url,
            candidate_entity_id=candidate_entity_id,
            social_profile_id=profile.id if profile else None,
            confidence=finding.confidence,
            confidence_reasons=[
                f"{source_label} publishes this address on the record itself",
                "Published by the source; not derived from the person's name",
            ],
            extraction_reason=(
                f"Read from the {source_label} record's own email field. No address "
                f"is ever constructed from a name and a domain."
            ),
            retrieved_at=moment,
        )
        counts["contacts"] += 1

    website = _first(data, WEBSITE_KEYS)
    if website and website.startswith(("http://", "https://")):
        classified = classify_url(website)
        # A personal site is a website; a link to a social account is a profile
        # and belongs in the profile list, not the contact list.
        if classified is None or not classified.is_social:
            record_contact(
                session,
                case_id=case_id,
                contact_type=ContactType.WEBSITE,
                value=website,
                source_name=source_label,
                collector=source,
                evidence_class=evidence_class,
                source_url=url,
                candidate_entity_id=candidate_entity_id,
                social_profile_id=profile.id if profile else None,
                confidence=finding.confidence,
                confidence_reasons=[
                    f"Linked from the {source_label} record as the subject's own site"
                ],
                extraction_reason=f"Read from the {source_label} record's own website field.",
                retrieved_at=moment,
            )
            counts["contacts"] += 1
        else:
            promoted = record_profile(
                session,
                case_id=case_id,
                target=target,
                url=website,
                collector=source,
                evidence_class=evidence_class,
                source_url=url,
                candidate_entity_id=candidate_entity_id,
                retrieved_at=moment,
            )
            if promoted is not None:
                counts["profiles"] += 1

    # 4. Everything the account published on its own profile page.
    _promote_readme(
        session,
        case_id=case_id,
        data=data,
        finding=finding,
        target=target,
        profile=profile,
        source=source,
        source_label=source_label,
        candidate_entity_id=candidate_entity_id,
        moment=moment,
        counts=counts,
    )
    return counts


def profile_facts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicitly stated facts a profile README carried, if any."""
    raw = data.get("readme_facts")
    if not isinstance(raw, list):
        return []
    return [fact for fact in raw if isinstance(fact, dict) and fact.get("value")]


def profile_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """The self-description block stored alongside a profile.

    Kept on the profile row rather than spread across new columns: it is a
    description of one page, it varies by platform, and a report reads it back
    whole. What matters is that it is *stored*, so the UI can show an
    investigator what the account says about itself without opening raw JSON.
    """
    facts = [fact for fact in profile_facts(data) if fact.get("kind") in DESCRIPTIVE_KINDS]
    relationship = data.get("name_relationship")
    repository = data.get("profile_repository")
    attributes: dict[str, Any] = {}
    if facts:
        attributes["profile_facts"] = facts
    if isinstance(relationship, dict) and relationship.get("relationship"):
        # Both names, side by side. The searched name is what the investigator
        # is looking for; the declared name is what the source claims. Storing
        # the pair is what lets a report explain the difference instead of
        # quietly preferring one.
        attributes["searched_name"] = data.get("searched_name")
        attributes["declared_name"] = data.get("declared_name")
        attributes["name_relationship"] = relationship
    if isinstance(repository, dict) and repository.get("exists"):
        attributes["profile_repository"] = repository
    if data.get("readme_url"):
        attributes["readme_url"] = data["readme_url"]
    if data.get("readme_note"):
        attributes["readme_note"] = data["readme_note"]
    if data.get("repository_description"):
        attributes["repository_description"] = data["repository_description"]
    return attributes


def _readme_evidence_id(
    session: Session,
    *,
    case_id: uuid.UUID,
    finding: Finding,
    data: dict[str, Any],
    source: str,
    moment: datetime,
) -> uuid.UUID | None:
    """Store a provenance descriptor for the README, and return its id.

    Content-addressed like every other artefact, so each promoted value points
    at a hash of exactly what was read. Re-running deduplicates on that hash
    rather than storing the page again.
    """
    readme_url = data.get("readme_url")
    if not readme_url:
        return None
    stored = EvidenceStore().store(
        session,
        case_id=case_id,
        collector=source,
        source_url=str(readme_url),
        content={
            "readme_url": readme_url,
            "readme_sha": data.get("readme_sha"),
            "profile_repository": data.get("profile_repository"),
            "extracted_facts": profile_facts(data),
            "extracted_emails": list(data.get("readme_emails") or []),
            "extracted_links": list(data.get("readme_links") or []),
            "extracted_images": list(data.get("readme_images") or []),
            "retrieved_at": moment.isoformat(),
            "extraction": (
                "Only values the README states explicitly, each carrying the source "
                "line it came from. Nothing is inferred and no contact is derived."
            ),
        },
        retrieved_at=moment,
        finding=finding,
    )
    return stored.evidence.id


def _promote_readme(
    session: Session,
    *,
    case_id: uuid.UUID,
    data: dict[str, Any],
    finding: Finding,
    target: Target | None,
    profile: SocialProfile | None,
    source: str,
    source_label: str,
    candidate_entity_id: uuid.UUID | None,
    moment: datetime,
    counts: dict[str, int],
) -> None:
    """Promote what an account published on its own profile page.

    A profile README is self-published, so a contact in it is
    ``PUBLIC_SELF_PUBLISHED`` whatever its domain looks like — classification
    follows provenance, not plausibility. A link in it is recorded as a profile
    with the README named as the page that published it, and a picture in it is
    recorded as page context. None of it is followed any further.
    """
    readme_url = str(data.get("readme_url") or "")
    if not readme_url:
        return

    evidence_id = _readme_evidence_id(
        session, case_id=case_id, finding=finding, data=data, source=source, moment=moment
    )
    origin = f"the public profile README of {data.get('login') or source_label}"

    for address in data.get("readme_emails") or []:
        if not isinstance(address, str) or "@" not in address:
            continue
        recorded = record_contact(
            session,
            case_id=case_id,
            contact_type=ContactType.EMAIL,
            value=address,
            source_name=f"{source_label} profile README",
            collector=source,
            evidence_class="page_fetched",
            classification=ContactClassification.PUBLIC_SELF_PUBLISHED,
            source_url=readme_url,
            candidate_entity_id=candidate_entity_id,
            social_profile_id=profile.id if profile else None,
            evidence_id=evidence_id,
            confidence=finding.confidence,
            confidence_reasons=[
                f"Written out in {origin}",
                "Published by the account holder; not derived from a name and a domain",
            ],
            extraction_reason=(
                f"Read verbatim from {origin}. The platform never constructs an address "
                f"from a person's name and an employer's domain."
            ),
            retrieved_at=moment,
        )
        if recorded is not None:
            counts["contacts"] += 1

    for link in data.get("readme_links") or []:
        if not isinstance(link, str) or not link.startswith(("http://", "https://")):
            continue
        classified = classify_url(link)
        if classified is not None and classified.is_social:
            promoted = record_profile(
                session,
                case_id=case_id,
                target=target,
                url=link,
                collector=source,
                evidence_class="page_fetched",
                source_url=readme_url,
                candidate_entity_id=candidate_entity_id,
                retrieved_at=moment,
                linked_from=origin,
            )
            if promoted is not None:
                counts["profiles"] += 1
            continue
        recorded = record_contact(
            session,
            case_id=case_id,
            contact_type=ContactType.WEBSITE,
            value=link,
            source_name=f"{source_label} profile README",
            collector=source,
            evidence_class="page_fetched",
            classification=ContactClassification.PUBLIC_SELF_PUBLISHED,
            source_url=readme_url,
            candidate_entity_id=candidate_entity_id,
            social_profile_id=profile.id if profile else None,
            evidence_id=evidence_id,
            confidence=finding.confidence,
            confidence_reasons=[f"Linked from {origin}"],
            extraction_reason=f"Read from a link published in {origin}.",
            retrieved_at=moment,
        )
        if recorded is not None:
            counts["contacts"] += 1

    for image_url in data.get("readme_images") or []:
        if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")):
            continue
        record_image(
            session,
            case_id=case_id,
            image_url=image_url,
            # The README is the page that gives the picture its meaning, so it
            # is the source page — not the profile URL, and not the image alone.
            source_page_url=readme_url,
            origin=source,
            evidence_class="page_fetched",
            platform=(profile.platform if profile else None),
            caption=f"Image published in {origin}",
            candidate_entity_id=candidate_entity_id,
            social_profile_id=profile.id if profile else None,
            fetch={
                "fetch_state": ImageFetchState.REFERENCE_ONLY,
                "fetch_note": (
                    f"Referenced by {origin}. The bytes were not downloaded during "
                    f"collection; fetch the image to hash it."
                ),
                "sha256": None,
                "content_type": None,
                "byte_length": None,
                "width": None,
                "height": None,
                "redirects": [],
                "final_url": None,
            },
            retrieved_at=moment,
        )
        counts["images"] += 1


def contacts_for_case(
    session: Session, case_id: uuid.UUID, *, candidate_entity_id: uuid.UUID | None = None
) -> list[PublicContact]:
    """Public contacts in the case, best-established first."""
    query = select(PublicContact).where(PublicContact.case_id == case_id)
    if candidate_entity_id is not None:
        query = query.where(PublicContact.candidate_entity_id == candidate_entity_id)
    order = {
        ContactClassification.VERIFIED_PUBLIC_BUSINESS: 0,
        ContactClassification.PUBLIC_PROFESSIONAL: 1,
        ContactClassification.PUBLIC_SELF_PUBLISHED: 2,
        ContactClassification.UNVERIFIED_PUBLIC_REFERENCE: 3,
    }
    rows = list(session.scalars(query))
    return sorted(rows, key=lambda row: (order.get(row.classification, 9), row.value))


def candidate_for_finding(session: Session, case_id: uuid.UUID, url: str) -> Entity | None:
    """The candidate entity extraction created for this page, if it exists yet."""
    return session.scalar(
        select(Entity).where(
            Entity.case_id == case_id,
            Entity.canonical_value == f"person-candidate:{url}",
        )
    )
