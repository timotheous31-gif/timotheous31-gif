"""Chronological events derived from findings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case


class TimelineEvent(UUIDMixin, TimestampMixin, Base):
    """One dated point in the investigation narrative."""

    __tablename__ = "timeline_events"
    __table_args__ = (
        Index("ix_timeline_case_occurred", "case_id", "occurred_at"),
        Index("ix_timeline_case_kind", "case_id", "kind"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("findings.id", ondelete="CASCADE"), default=None, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="SET NULL"), default=None, index=True
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: True when the date is approximate (e.g. year-only archive snapshots).
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    collector: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    case: Mapped[Case] = relationship(back_populates="timeline_events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TimelineEvent {self.occurred_at:%Y-%m-%d} {self.title[:40]!r}>"
