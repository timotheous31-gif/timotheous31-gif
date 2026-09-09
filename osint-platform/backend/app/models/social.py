"""Persisted social profiles, public image evidence, and analyst decisions.

Three tables, and the reasons they are separate from what already exists:

* **social_profiles** — a public profile page attributed (or not yet attributed)
  to a candidate. The in-memory classification in ``app.collectors.social``
  says what a URL *is*; this records that we saw one, what it said, and how
  strongly it connects to a candidate.

* **image_evidence** — a public image, stored as *page context*. Never as
  biometric proof. Whether the bytes were actually fetched is a column, because
  "we hashed these bytes" and "we recorded this URL" are different claims.

* **analyst_decisions** — a human's judgement, kept in its own table rather
  than as columns on the rows it judges. That is the point: automated
  confidence and analyst decision must never overwrite one another, and putting
  them in separate tables makes that structurally impossible instead of a rule
  somebody has to remember.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    AnalystDecision,
    ContactClassification,
    ContactType,
    DecisionSubject,
    ImageFetchState,
    ProfileAccess,
)
from app.models.types import GUID, JSONType


class SocialProfile(UUIDMixin, TimestampMixin, Base):
    """A public profile page, and how strongly it ties to a candidate."""

    __tablename__ = "social_profiles"
    __table_args__ = (
        # One row per profile URL per case: the URL is the profile's identity,
        # exactly as it is for a person candidate. Two investigators importing
        # the same LinkedIn page must not create two profiles.
        UniqueConstraint("case_id", "profile_url", name="uq_social_profile_case_url"),
        Index("ix_social_profiles_case_platform", "case_id", "platform"),
        Index("ix_social_profiles_candidate", "candidate_entity_id"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The candidate this profile is attributed to. Nullable on purpose: a
    #: profile can be recorded before anyone decides whose it is, and clearing
    #: the attribution must not delete the evidence.
    candidate_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="SET NULL"), default=None
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("targets.id", ondelete="SET NULL"), default=None
    )

    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    platform_label: Mapped[str] = mapped_column(String(100), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
    profile_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(300), default=None)
    bio: Mapped[str | None] = mapped_column(Text, default=None)

    #: Where we learned of this profile — a search result page, an API record.
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    accessibility: Mapped[ProfileAccess] = mapped_column(
        SAEnum(ProfileAccess, name="profile_access", native_enum=False, length=20),
        default=ProfileAccess.UNKNOWN,
        nullable=False,
    )
    #: False when the platform refuses anonymous server-side requests. Recorded
    #: so a report can say why nothing was fetched, rather than implying we chose
    #: not to look.
    server_fetchable: Mapped[bool] = mapped_column(default=True, nullable=False)
    fetch_note: Mapped[str | None] = mapped_column(Text, default=None)

    collector: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False)

    #: What the platform computed. An analyst decision never edits this.
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    match_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    mismatch_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    corroborated_by: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)

    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    # What a profile page states about itself lives in ``attributes`` rather
    # than in six more columns: it is one page's self-description, it differs by
    # platform, and every reader wants it whole. These properties are the
    # supported way in, so the API, the report and the UI all read the same
    # keys instead of each reaching into the JSON with its own spelling.

    @property
    def profile_facts(self) -> list[dict]:
        """Explicit statements read from the profile page, each with its line."""
        facts = (self.attributes or {}).get("profile_facts")
        return [fact for fact in facts if isinstance(fact, dict)] if isinstance(facts, list) else []

    @property
    def declared_name(self) -> str | None:
        """The name the source declares. Never written back onto the target."""
        return (self.attributes or {}).get("declared_name")

    @property
    def searched_name(self) -> str | None:
        """The name the investigation is actually looking for."""
        return (self.attributes or {}).get("searched_name")

    @property
    def name_relationship(self) -> dict | None:
        """How the declared name relates to the searched one, and why."""
        value = (self.attributes or {}).get("name_relationship")
        return value if isinstance(value, dict) else None

    @property
    def detail_source_url(self) -> str | None:
        """The page the statements above were read from."""
        return (self.attributes or {}).get("readme_url")

    @property
    def detail_note(self) -> str | None:
        """Why there are no statements, when there are none."""
        return (self.attributes or {}).get("readme_note")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SocialProfile {self.platform}:{self.handle or self.profile_url}>"


class ImageEvidence(UUIDMixin, TimestampMixin, Base):
    """A public image recorded as page context.

    What this establishes is that a picture appears on a page associated with a
    candidate. It establishes nothing about who is depicted, and the platform
    performs no facial or biometric analysis of any kind — there is no column
    here that could hold such a result, which is deliberate.
    """

    __tablename__ = "image_evidence"
    __table_args__ = (
        UniqueConstraint("case_id", "image_url", "source_page_url", name="uq_image_case_url_page"),
        Index("ix_image_evidence_case_candidate", "case_id", "candidate_entity_id"),
        Index("ix_image_evidence_case_platform", "case_id", "platform"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="SET NULL"), default=None
    )
    social_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("social_profiles.id", ondelete="SET NULL"), default=None
    )

    image_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    #: The page the image appears on. This is the evidence; the image alone is
    #: not, because a bare image URL says nothing about context.
    source_page_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(100), default=None)
    caption: Mapped[str | None] = mapped_column(Text, default=None)
    #: Public text surrounding the image, when the source supplies it.
    context_text: Mapped[str | None] = mapped_column(Text, default=None)

    fetch_state: Mapped[ImageFetchState] = mapped_column(
        SAEnum(ImageFetchState, name="image_fetch_state", native_enum=False, length=20),
        default=ImageFetchState.REFERENCE_ONLY,
        nullable=False,
        index=True,
    )
    #: Only meaningful when fetch_state is FETCHED: the hash is of bytes this
    #: platform actually read, and claiming one for a URL we never fetched would
    #: be a fabricated integrity guarantee.
    sha256: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    content_type: Mapped[str | None] = mapped_column(String(128), default=None)
    byte_length: Mapped[int | None] = mapped_column(Integer, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Every hop, so a reviewer can see where the bytes actually came from.
    redirects: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    #: Why nothing was fetched, when nothing was.
    fetch_note: Mapped[str | None] = mapped_column(Text, default=None)

    origin: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("evidence.id", ondelete="SET NULL"), default=None
    )
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ImageEvidence {self.fetch_state} {self.image_url[:60]}>"


class AnalystDecisionRecord(UUIDMixin, TimestampMixin, Base):
    """A human judgement about one association.

    Kept apart from the row it judges so that recording a decision cannot touch
    automated confidence. One current decision per subject; the change history
    is the row's ``updated_at`` plus the note.
    """

    __tablename__ = "analyst_decisions"
    __table_args__ = (
        UniqueConstraint("case_id", "subject_type", "subject_id", name="uq_decision_case_subject"),
        Index("ix_analyst_decisions_case_decision", "case_id", "decision"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_type: Mapped[DecisionSubject] = mapped_column(
        SAEnum(DecisionSubject, name="decision_subject", native_enum=False, length=30),
        nullable=False,
    )
    #: Not a foreign key: the subject is one of three tables. Referential
    #: integrity is provided by the case cascade — every subject is case-scoped,
    #: so a deleted case takes its decisions with it.
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)

    decision: Mapped[AnalystDecision] = mapped_column(
        SAEnum(AnalystDecision, name="analyst_decision", native_enum=False, length=20),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    decided_by: Mapped[str | None] = mapped_column(String(200), default=None)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AnalystDecision {self.subject_type}:{self.decision}>"


class PublicContact(UUIDMixin, TimestampMixin, Base):
    """A publicly published professional or business contact point.

    Deliberately *public and professional only*. There is no field here for a
    residential address, and no code path that derives an address from a name
    and a domain — a plausible guess presented beside real evidence is worse
    than no answer, because it reads as a finding.

    Shaped like ``social_profiles`` on purpose: same candidate attribution, same
    separation of automated confidence from analyst judgement, same provenance.
    A parallel structure would have been a second way to say the same thing.
    """

    __tablename__ = "public_contacts"
    __table_args__ = (
        # One row per value per kind per case: the same address found by two
        # collectors is one contact corroborated twice, not two contacts.
        UniqueConstraint("case_id", "contact_type", "value", name="uq_public_contact_case_value"),
        Index("ix_public_contacts_case_type", "case_id", "contact_type"),
        Index("ix_public_contacts_candidate", "candidate_entity_id"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="SET NULL"), default=None
    )
    social_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("social_profiles.id", ondelete="SET NULL"), default=None
    )

    contact_type: Mapped[ContactType] = mapped_column(
        SAEnum(ContactType, name="contact_type", native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300), default=None)
    classification: Mapped[ContactClassification] = mapped_column(
        SAEnum(
            ContactClassification,
            name="contact_classification",
            native_enum=False,
            length=40,
        ),
        default=ContactClassification.UNVERIFIED_PUBLIC_REFERENCE,
        nullable=False,
        index=True,
    )

    #: Where it was published. A contact with no source is not evidence.
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    collector: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("evidence.id", ondelete="SET NULL"), default=None
    )

    #: What the platform computed. An analyst decision never edits it.
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    #: Why this value was promoted out of a raw payload, so a reader can audit
    #: the transformation rather than trusting it.
    extraction_reason: Mapped[str | None] = mapped_column(Text, default=None)

    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PublicContact {self.contact_type}:{self.value[:40]}>"
