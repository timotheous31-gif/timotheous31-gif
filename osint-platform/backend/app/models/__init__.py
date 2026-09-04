"""SQLAlchemy models.

Importing this package registers every mapper on ``Base.metadata`` — Alembic's
autogenerate and the test fixtures both rely on that.
"""

from __future__ import annotations

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow
from app.models.case import Case
from app.models.collection import CollectorRun, Evidence, Finding, finding_evidence
from app.models.entity import Entity, Relationship, entity_sources, relationship_evidence
from app.models.enums import (
    CaseStatus,
    Classification,
    EntityType,
    FindingKind,
    JobState,
    MatchStrength,
    RelationshipType,
    ReportFormat,
    RunStatus,
    TargetStatus,
    TargetType,
)
from app.models.job import Job
from app.models.tag import Tag, case_tags, target_tags
from app.models.target import Target
from app.models.timeline import TimelineEvent

__all__ = [
    "Base",
    "Case",
    "CaseStatus",
    "Classification",
    "CollectorRun",
    "Entity",
    "EntityType",
    "Evidence",
    "Finding",
    "FindingKind",
    "Job",
    "JobState",
    "MatchStrength",
    "Relationship",
    "RelationshipType",
    "ReportFormat",
    "RunStatus",
    "Tag",
    "Target",
    "TargetStatus",
    "TargetType",
    "TimelineEvent",
    "TimestampMixin",
    "UUIDMixin",
    "case_tags",
    "entity_sources",
    "finding_evidence",
    "relationship_evidence",
    "target_tags",
    "utcnow",
]
