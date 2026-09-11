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
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    Classification,
    FindingKind,
    ObservationStage,
    ObservationSubject,
    RunStatus,
)
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.target import Target


#: One stored artefact can support several findings — a single HTTP response
#: yields page metadata, security headers and outbound links — and one finding
#: can rest on several artefacts. A join table is the only shape that keeps
#: both deduplication and complete provenance.
finding_evidence = Table(
    "finding_evidence",
    Base.metadata,
    Column("finding_id", GUID(), ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True),
    Column("evidence_id", GUID(), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
)


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
    #: How the collector described its source at the time it ran. Recorded here
    #: so a report never has to consult the registry of whichever process
    #: happens to render it.
    source_attribution: Mapped[str | None] = mapped_column(String(200), default=None)
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
        secondary=finding_evidence, back_populates="findings", lazy="selectin"
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
    findings: Mapped[list[Finding]] = relationship(
        secondary=finding_evidence, back_populates="evidence", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Evidence {self.collector} {self.sha256[:12]}>"


class ExecutionObservation(UUIDMixin, TimestampMixin, Base):
    """Immutable record that one execution observed one object, in one state.

    The auditability problem this closes: a case's findings, entities, profiles,
    contacts, images and evidence are *canonical and mutable*. A finding is
    deduplicated across the whole case, so a rerun returns the existing row and
    its ``run_id`` keeps pointing at the first run that ever produced it. Asking
    "what did execution X observe, and what report did execution X produce?" had
    no answer, and a report generated for an old execution silently showed
    whatever a later one had since discovered and rescored.

    "Execution X saw object O" is a different fact from "object O exists in this
    case", with a different cardinality — many executions, one object — so it gets
    its own row rather than another column on the object. And because the *state*
    an object was in matters as much as the fact it was seen, each row carries a
    snapshot in ``state``: the score, the reasons, the payload as that execution
    left them. An execution report renders from these snapshots, never from the
    canonical row's current columns, which is precisely what makes a historical
    report stable when a later execution changes the case.

    Three rules hold these rows honest:

    * **Append-only.** Nothing rewrites an observation after its execution ends.
      One row per (execution, subject) — a rerun adds a row, it does not edit one.
    * **No backfill.** Rows that predate this table are not invented into an
      execution. A job with no observations is reported as having no ledger, not
      as having found nothing.
    * **Never authoritative for the present.** Current case state is still the
      canonical tables. These rows say what *was*, not what is.
    """

    __tablename__ = "execution_observations"
    __table_args__ = (
        # One observation per execution per object. A rerun that sees the same
        # page again updates that execution's single row for it rather than
        # accumulating duplicates within one run.
        UniqueConstraint(
            "job_id", "subject_type", "subject_id", name="uq_observation_execution_subject"
        ),
        Index("ix_observations_case_subject", "case_id", "subject_type", "subject_id"),
        Index("ix_observations_job_kind", "job_id", "subject_type"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The execution. Nullable because the engine can be driven without a tracked
    #: job (the CLI, a direct call) and inventing one would be a lie; such rows are
    #: excluded from every execution report, and the report says so.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), default=None, index=True
    )
    #: The collector run within that execution, where one applies. Promotion and
    #: correlation derive from findings rather than from a request, so they have
    #: none.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("collector_runs.id", ondelete="SET NULL"), default=None
    )

    stage: Mapped[ObservationStage] = mapped_column(
        SAEnum(ObservationStage, name="observation_stage", native_enum=False, length=24),
        nullable=False,
    )
    subject_type: Mapped[ObservationSubject] = mapped_column(
        SAEnum(ObservationSubject, name="observation_subject", native_enum=False, length=30),
        nullable=False,
    )
    #: Not a foreign key: the subject is one of seven case-scoped tables, exactly
    #: as in ``analyst_decisions``. The case cascade provides the integrity.
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)

    collector: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: True when this execution is the one that created the canonical row. Gives
    #: first-seen/last-seen execution semantics without two more columns on every
    #: table: first-seen is the observation carrying this flag, last-seen is the
    #: newest observation for the subject.
    first_seen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: The subject's state as this execution left it. The snapshot an execution
    #: report renders from.
    state: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ExecutionObservation {self.stage} {self.subject_type}:{self.subject_id}>"
