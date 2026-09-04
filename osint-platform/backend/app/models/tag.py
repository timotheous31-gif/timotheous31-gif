"""Free-form labels attached to cases and targets."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.types import GUID

case_tags = Table(
    "case_tags",
    Base.metadata,
    Column("case_id", GUID(), ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", GUID(), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

target_tags = Table(
    "target_tags",
    Base.metadata,
    Column("target_id", GUID(), ForeignKey("targets.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", GUID(), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(UUIDMixin, TimestampMixin, Base):
    """A reusable label."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tag {self.name}>"
