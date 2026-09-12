"""SQLAlchemy models.

Importing this package registers every mapper on ``Base.metadata`` — Alembic's
autogenerate and the test fixtures both rely on that.
"""

from __future__ import annotations

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow
from app.models.case import Case
from app.models.collection import (
    CollectorRun,
    Evidence,
    ExecutionObservation,
    Finding,
    finding_evidence,
)
from app.models.entity import Entity, Relationship, entity_sources, relationship_evidence
from app.models.enums import (
    AnalystDecision,
    CaseStatus,
    Classification,
    ContactClassification,
    ContactType,
    DecisionSubject,
    EntityType,
    FindingKind,
    ImageFetchState,
    JobState,
    MatchStrength,
    ObservationStage,
    ObservationSubject,
    ProfileAccess,
    RelationshipType,
    ReportFormat,
    RunStatus,
    TargetStatus,
    TargetType,
)
from app.models.job import Job
from app.models.social import (
    AnalystDecisionRecord,
    ImageEvidence,
    PublicContact,
    SocialProfile,
)
from app.models.tag import Tag, case_tags, target_tags
from app.models.target import Target
from app.models.timeline import TimelineEvent

__all__ = [
    "AnalystDecision",
    "AnalystDecisionRecord",
    "Base",
    "Case",
    "CaseStatus",
    "Classification",
    "CollectorRun",
    "ContactClassification",
    "ContactType",
    "DecisionSubject",
    "Entity",
    "EntityType",
    "Evidence",
    "ExecutionObservation",
    "Finding",
    "FindingKind",
    "ImageEvidence",
    "ImageFetchState",
    "Job",
    "JobState",
    "MatchStrength",
    "ObservationStage",
    "ObservationSubject",
    "ProfileAccess",
    "PublicContact",
    "Relationship",
    "RelationshipType",
    "ReportFormat",
    "RunStatus",
    "SocialProfile",
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
