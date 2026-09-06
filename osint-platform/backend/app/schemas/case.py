"""Case and target schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CaseStatus, TargetStatus, TargetType


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class CaseCreate(BaseModel):
    """Payload for creating an investigation."""

    name: str = Field(min_length=1, max_length=200, examples=["Example Domain Investigation"])
    description: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned = {tag.strip().lower() for tag in value if tag and tag.strip()}
        for tag in cleaned:
            if len(tag) > 64:
                raise ValueError("tags must be 64 characters or fewer")
        return sorted(cleaned)


class CaseUpdate(BaseModel):
    """Partial update. Unset fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=20000)
    status: CaseStatus | None = None
    tags: list[str] | None = Field(default=None, max_length=32)


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: CaseStatus
    notes: str | None
    tags: list[TagRead]
    created_at: datetime
    updated_at: datetime


class CaseSummary(BaseModel):
    """Case plus the counters the dashboard shows on the overview page."""

    case: CaseRead
    targets: int
    findings: int
    entities: int
    relationships: int
    evidence: int
    timeline_events: int
    collectors_run: list[str]
    confidence_distribution: dict[str, int]
    last_run_at: datetime | None = None


class PersonContext(BaseModel):
    """Optional, user-supplied context that narrows a PERSON investigation.

    Everything here is something the investigator already knows and is willing
    to state. It is never inferred, never derived from a collector, and never
    used to *find* new personal information — only to judge whether a candidate
    that a public source returned is plausibly the same person. That is the
    difference between narrowing a name search and enriching a dossier.

    Deliberately absent: date of birth, address, phone number, employer history
    and anything else whose only purpose would be to identify a private person
    more precisely than public sources already do.
    """

    #: Handles the investigator already knows. Checked directly; never guessed.
    known_usernames: list[str] = Field(default_factory=list, max_length=20)
    #: Public profile URLs the investigator already has.
    profile_urls: list[str] = Field(default_factory=list, max_length=20)
    #: Employers, institutions or groups, used to corroborate affiliations.
    organizations: list[str] = Field(default_factory=list, max_length=20)
    schools: list[str] = Field(default_factory=list, max_length=20)
    #: Coarse location only. City and country corroborate; nothing finer is
    #: accepted, because a street address is not corroboration, it is tracking.
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)

    def is_empty(self) -> bool:
        return not any(
            (
                self.known_usernames,
                self.profile_urls,
                self.organizations,
                self.schools,
                self.country,
                self.city,
            )
        )


class TargetCreate(BaseModel):
    """Add a target to a case.

    ``type`` may be omitted for input whose shape identifies it — a domain,
    URL, IP address, email address, ``owner/repo`` or ``@handle``. A bare name
    is not such a shape: it is rejected as ambiguous, and must be resent with
    ``type`` set to PERSON or ORGANIZATION.
    """

    value: str = Field(min_length=1, max_length=1024, examples=["example.com"])
    type: TargetType | None = None
    notes: str | None = Field(default=None, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    #: Only meaningful for PERSON targets; ignored for every other type.
    context: PersonContext | None = None


class TargetBulkCreate(BaseModel):
    targets: list[TargetCreate] = Field(min_length=1, max_length=200)


class TargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    type: TargetType
    raw_input: str
    normalized_value: str
    status: TargetStatus
    notes: str | None
    attributes: dict
    tags: list[TagRead]
    created_at: datetime
    updated_at: datetime


class TargetUpdate(BaseModel):
    notes: str | None = Field(default=None, max_length=5000)
    status: TargetStatus | None = None
    tags: list[str] | None = Field(default=None, max_length=32)


class NormalizationPreview(BaseModel):
    """What the platform would store for a given raw input.

    When the input is name-shaped free text its type cannot be inferred, so
    ``ambiguous`` is true, ``type`` and ``normalized_value`` are empty, and
    ``candidates`` lists the types the caller must choose between. The preview
    reports that state rather than erroring, so a UI can offer the choice.
    """

    raw_input: str
    type: TargetType | None = None
    normalized_value: str = ""
    attributes: dict = Field(default_factory=dict)
    ambiguous: bool = False
    candidates: list[TargetType] = Field(default_factory=list)
    message: str = ""
