"""Entities and the typed relationships between them."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
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
from app.models.enums import EntityType, MatchStrength, RelationshipType
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.collection import Finding

#: Provenance: which findings support the existence of an entity.
entity_sources = Table(
    "entity_sources",
    Base.metadata,
    Column("entity_id", GUID(), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
    Column("finding_id", GUID(), ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True),
)

#: Provenance: which findings support a relationship.
relationship_evidence = Table(
    "relationship_evidence",
    Base.metadata,
    Column(
        "relationship_id",
        GUID(),
        ForeignKey("relationships.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("finding_id", GUID(), ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True),
)


class Entity(UUIDMixin, TimestampMixin, Base):
    """A resolved thing: a domain, a username, a repository, an organisation."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("case_id", "type", "canonical_value", name="uq_entity_case_value"),
        Index("ix_entities_case_type", "case_id", "type"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[EntityType] = mapped_column(
        SAEnum(EntityType, name="entity_type", native_enum=False, length=30),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_value: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    aliases: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False, index=True)
    confidence_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    case: Mapped[Case] = relationship(back_populates="entities")
    sources: Mapped[list[Finding]] = relationship(secondary=entity_sources, lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Entity {self.type}:{self.canonical_value}>"


class Relationship(UUIDMixin, TimestampMixin, Base):
    """A typed, evidence-backed edge between two entities.

    A relationship never stands on its own: ``confidence_reasons`` explains why
    the edge exists and ``evidence`` links to the findings that support it. In
    particular, a shared username produces ``SAME_USERNAME`` — never an
    assertion that the accounts belong to the same person.
    """

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_entity_id", "target_entity_id", "type", name="uq_relationship_edge"
        ),
        Index("ix_relationships_case_type", "case_id", "type"),
        Index("ix_relationships_confidence", "case_id", "confidence"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[RelationshipType] = mapped_column(
        SAEnum(RelationshipType, name="relationship_type", native_enum=False, length=30),
        nullable=False,
        index=True,
    )

    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence_reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    strength: Mapped[MatchStrength] = mapped_column(
        SAEnum(MatchStrength, name="match_strength", native_enum=False, length=30),
        default=MatchStrength.WEAK_ASSOCIATION,
        nullable=False,
    )

    collector: Mapped[str] = mapped_column(String(64), default="correlation", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    case: Mapped[Case] = relationship(back_populates="relationships")
    source_entity: Mapped[Entity] = relationship(foreign_keys=[source_entity_id], lazy="joined")
    target_entity: Mapped[Entity] = relationship(foreign_keys=[target_entity_id], lazy="joined")
    evidence: Mapped[list[Finding]] = relationship(secondary=relationship_evidence, lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Relationship {self.type} conf={self.confidence:.2f}>"
