"""Investigation cases: the container for everything a study produces."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import CaseStatus
from app.models.tag import Tag, case_tags
from app.models.types import GUID

if TYPE_CHECKING:
    from app.models.auth import Workspace
    from app.models.collection import Evidence, Finding
    from app.models.entity import Entity, Relationship
    from app.models.job import Job
    from app.models.target import Target
    from app.models.timeline import TimelineEvent


class Case(UUIDMixin, TimestampMixin, Base):
    """An investigation."""

    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_status_created", "status", "created_at"),
        Index("ix_cases_workspace_created", "workspace_id", "created_at"),
    )

    #: The tenant boundary. Every other table in the platform reaches a
    #: workspace through its ``case_id``, so authorizing here authorizes all of
    #: them at once.
    #:
    #: Nullable for exactly one reason: an installation that predates workspaces
    #: has cases with no owner, and guessing one would be worse than admitting
    #: it. Such a case is **unclaimed** — invisible to every API route, and
    #: adopted deliberately by an operator running ``claim-cases``. See
    #: ``docs/pilot-deployment.md``.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT", name="fk_cases_workspace_id"),
        default=None,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[CaseStatus] = mapped_column(
        SAEnum(CaseStatus, name="case_status", native_enum=False, length=20),
        default=CaseStatus.NEW,
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    workspace: Mapped[Workspace | None] = relationship(back_populates="cases", lazy="joined")
    tags: Mapped[list[Tag]] = relationship(secondary=case_tags, lazy="selectin")
    targets: Mapped[list[Target]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )
    entities: Mapped[list[Entity]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )
    relationships: Mapped[list[Relationship]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )
    timeline_events: Mapped[list[TimelineEvent]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )
    jobs: Mapped[list[Job]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Case {self.name!r} status={self.status}>"
