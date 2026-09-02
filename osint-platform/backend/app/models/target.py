"""Investigation targets, always stored in normalised form."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import TargetStatus, TargetType
from app.models.tag import Tag, target_tags
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.collection import CollectorRun


class Target(UUIDMixin, TimestampMixin, Base):
    """One thing under investigation within a case."""

    __tablename__ = "targets"
    __table_args__ = (
        UniqueConstraint("case_id", "type", "normalized_value", name="uq_target_case_value"),
        Index("ix_targets_case_status", "case_id", "status"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[TargetType] = mapped_column(
        SAEnum(TargetType, name="target_type", native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    raw_input: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    status: Mapped[TargetStatus] = mapped_column(
        SAEnum(TargetStatus, name="target_status", native_enum=False, length=20),
        default=TargetStatus.PENDING,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    #: Structured extras produced by normalisation (e.g. URL parts, email domain).
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    case: Mapped[Case] = relationship(back_populates="targets")
    tags: Mapped[list[Tag]] = relationship(secondary=target_tags, lazy="selectin")
    runs: Mapped[list[CollectorRun]] = relationship(
        back_populates="target", cascade="all, delete-orphan", lazy="noload"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Target {self.type}:{self.normalized_value}>"
