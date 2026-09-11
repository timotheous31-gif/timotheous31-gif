"""Schemas for social profiles, image evidence and analyst decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalystDecision, ContactClassification, ContactType, DecisionSubject


class AnalystDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_type: DecisionSubject
    subject_id: uuid.UUID
    decision: AnalystDecision
    note: str | None
    decided_by: str | None
    decided_at: datetime


class AnalystDecisionWrite(BaseModel):
    """Record an analyst's judgement about one association."""

    subject_type: DecisionSubject
    subject_id: uuid.UUID
    decision: AnalystDecision
    note: str | None = Field(default=None, max_length=4000)
    decided_by: str | None = Field(default=None, max_length=200)


class SocialProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_entity_id: uuid.UUID | None
    platform: str
    platform_label: str
    handle: str | None
    profile_url: str
    display_name: str | None
    bio: str | None
    source_url: str | None
    accessibility: str
    server_fetchable: bool
    fetch_note: str | None
    collector: str
    evidence_class: str
    #: What the platform computed. An analyst decision never changes it.
    confidence: float
    match_reasons: list[str]
    mismatch_reasons: list[str]
    corroborated_by: list[str]
    retrieved_at: datetime | None
    #: How this profile came to be in the case, and the page that published it
    #: when one did. A reader weighs "you supplied this handle" differently
    #: from "a name search returned it".
    discovery_method: str | None = None
    #: Every route, strongest first. ``discovery_method`` is this list's head.
    discovery_methods: list[str] = []
    discovered_from: str | None = None
    discovered_from_all: list[str] = []
    #: What the account states about itself on its own profile page. Explicit
    #: statements only, each carrying the line it was read from.
    profile_facts: list[dict] = []
    #: The declared name beside the searched one. Shown together, never
    #: substituted: the target keeps the name the investigator gave it.
    declared_name: str | None = None
    searched_name: str | None = None
    name_relationship: dict | None = None
    detail_source_url: str | None = None
    detail_note: str | None = None
    #: The analyst's separate judgement, when one has been recorded.
    decision: AnalystDecisionRead | None = None


class ImageEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_entity_id: uuid.UUID | None
    social_profile_id: uuid.UUID | None
    image_url: str
    source_page_url: str
    platform: str | None
    caption: str | None
    context_text: str | None
    #: FETCHED | REFERENCE_ONLY | BLOCKED — whether these bytes were read.
    fetch_state: str
    #: Present only for FETCHED: the hash is of bytes this platform read.
    sha256: str | None
    content_type: str | None
    byte_length: int | None
    width: int | None
    height: int | None
    redirects: list[str]
    final_url: str | None
    fetch_note: str | None
    origin: str
    evidence_class: str
    retrieved_at: datetime | None
    evidence_id: uuid.UUID | None
    #: Restated on every record: no facial or biometric analysis is performed.
    attributes: dict
    decision: AnalystDecisionRead | None = None


class PublicContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_entity_id: uuid.UUID | None
    social_profile_id: uuid.UUID | None
    contact_type: ContactType
    value: str
    label: str | None
    classification: ContactClassification
    source_url: str | None
    source_name: str
    collector: str
    evidence_class: str
    evidence_id: uuid.UUID | None
    #: What the platform computed. An analyst decision never changes it.
    confidence: float
    confidence_reasons: list[str]
    #: Why this value was promoted out of a raw payload, so the transformation
    #: can be audited rather than trusted.
    extraction_reason: str | None
    retrieved_at: datetime | None
    attributes: dict
    decision: AnalystDecisionRead | None = None


class CandidateGroup(BaseModel):
    """One candidate with everything attributed to it.

    Grouped by candidate and source, never by visual similarity: the platform
    does not compare images, so it has no basis on which to group them that way.
    """

    entity_id: uuid.UUID | None
    display_name: str
    canonical_value: str
    #: Computed by the platform, independent of any analyst decision.
    confidence: float
    confidence_reasons: list[str]
    match_reasons: list[str]
    mismatch_reasons: list[str]
    corroborated_by: list[str]
    identity_established: bool = False
    social_profiles: list[SocialProfileRead] = Field(default_factory=list)
    images: list[ImageEvidenceRead] = Field(default_factory=list)
    public_contacts: list[PublicContactRead] = Field(default_factory=list)
    decision: AnalystDecisionRead | None = None


class FetchImageRequest(BaseModel):
    """Ask the platform to fetch a referenced image's bytes."""

    #: Refuse politely rather than fetching when the source platform blocks
    #: anonymous requests; set false only to retry a transient failure.
    respect_platform_block: bool = True
