"""Background job tracking, mirroring Celery task state into the database."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import JobState
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case


class Job(UUIDMixin, TimestampMixin, Base):
    """An investigation run tracked from queue to completion."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_case_state", "case_id", "state"),)

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    celery_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    state: Mapped[JobState] = mapped_column(
        SAEnum(JobState, name="job_state", native_enum=False, length=20),
        default=JobState.QUEUED,
        nullable=False,
        index=True,
    )
    #: 0.0 - 1.0. Collectors emit progress as they finish.
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(500), default=None)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    error_type: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    #: Requested parameters (targets, collector include/exclude lists).
    params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    #: Summary counters produced by the run.
    result: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    #: Set by the API; the worker checks it between collectors.
    cancel_requested: Mapped[bool] = mapped_column(default=False, nullable=False)

    case: Mapped[Case] = relationship(back_populates="jobs")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Job {self.id} {self.state} {self.progress:.0%}>"
