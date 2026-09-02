"""Collection-side models: collector runs, findings and evidence.

The chain is deliberate: a *run* records that a collector executed, a *finding*
is one normalised assertion produced by that run, and *evidence* is the
hash-verified provenance for that finding. Nothing is shown in a report that
cannot be traced back through this chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import Classification, FindingKind, RunStatus
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.target import Target


class CollectorRun(UUIDMixin, TimestampMixin, Base):
    """One execution of one collector against one target."""

    __tablename__ = "collector_runs"
    __table_args__ = (
        Index("ix_runs_case_collector", "case_id", "collector"),
        Index("ix_runs_target_status", "target_id", "status"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="SET NULL"), default=None, index=True
    )

    collector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collector_version: Mapped[str] = mapped_column(String(32), default="0.0.0", nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status", native_enum=False, length=20),
        default=RunStatus.PENDING,
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[float | None] = mapped_column(Float, default=None)

    #: Populated only on failure. Errors are recorded, never swallowed.
    error_type: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    #: Free-form counters (requests made, records seen, findings produced).
    stats: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    target: Mapped[Target] = relationship(back_populates="runs")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="noload"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CollectorRun {self.collector} {self.status}>"


class Finding(UUIDMixin, TimestampMixin, Base):
    """One normalised assertion produced by a collector."""

    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_case_kind", "case_id", "kind"),
        Index("ix_findings_case_classification", "case_id", "classification"),
        Index("ix_findings_dedupe", "case_id", "kind", "dedupe_key"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("targets.id", ondelete="CASCADE"), default=None, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("collector_runs.id", ondelete="SET NULL"), default=None, index=True
    )

    kind: Mapped[FindingKind] = mapped_column(
        SAEnum(FindingKind, name="finding_kind", native_enum=False, length=40),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    #: Normalised payload. Already passed through the privacy filter.
    data: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    collector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)

    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False, index=True)
    confidence_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)

    classification: Mapped[Classification] = mapped_column(
        SAEnum(Classification, name="classification", native_enum=False, length=20),
        default=Classification.PUBLIC,
        nullable=False,
    )
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Stable identity of the assertion, used to avoid storing duplicates.
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    #: When the described fact occurred (registration date, commit date, ...).
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    case: Mapped[Case] = relationship(back_populates="findings")
    run: Mapped[CollectorRun | None] = relationship(back_populates="findings")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Finding {self.kind} {self.title[:40]!r}>"


class Evidence(UUIDMixin, TimestampMixin, Base):
    """Provenance for a finding: where it came from and proof it is unaltered."""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("case_id", "sha256", name="uq_evidence_case_hash"),
        Index("ix_evidence_case_collector", "case_id", "collector"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("findings.id", ondelete="CASCADE"), default=None, index=True
    )

    collector: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: SHA-256 of the stored payload — the integrity anchor for the report.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str | None] = mapped_column(String(128), default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Path (relative to EVIDENCE_DIR) of the stored raw payload, when kept.
    raw_ref: Mapped[str | None] = mapped_column(String(512), default=None)
    #: A short, privacy-filtered excerpt safe to render in a report.
    excerpt: Mapped[str | None] = mapped_column(Text, default=None)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    case: Mapped[Case] = relationship(back_populates="evidence")
    finding: Mapped[Finding | None] = relationship(back_populates="evidence")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Evidence {self.collector} {self.sha256[:12]}>"
