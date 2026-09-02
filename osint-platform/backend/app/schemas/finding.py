"""Finding, evidence, entity, relationship, timeline and job schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    Classification,
    EntityType,
    FindingKind,
    JobState,
    MatchStrength,
    RelationshipType,
    RunStatus,
)


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    collector: str
    source_url: str | None
    retrieved_at: datetime
    sha256: str
    content_type: str | None
    size_bytes: int
    excerpt: str | None
    redacted: bool
    created_at: datetime
    finding_ids: list[uuid.UUID] = Field(default_factory=list)

    @classmethod
    def from_evidence(cls, evidence) -> EvidenceRead:
        return cls(
            id=evidence.id,
            collector=evidence.collector,
            source_url=evidence.source_url,
            retrieved_at=evidence.retrieved_at,
            sha256=evidence.sha256,
            content_type=evidence.content_type,
            size_bytes=evidence.size_bytes,
            excerpt=evidence.excerpt,
            redacted=evidence.redacted,
            created_at=evidence.created_at,
            finding_ids=[finding.id for finding in (evidence.findings or [])],
        )


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    target_id: uuid.UUID | None
    run_id: uuid.UUID | None
    kind: FindingKind
    title: str
    summary: str | None
    data: dict
    collector: str
    source_url: str | None
    confidence: float
    confidence_reasons: list[str]
    classification: Classification
    redacted: bool
    observed_at: datetime | None
    created_at: datetime
    evidence: list[EvidenceRead] = Field(default_factory=list)


class CollectorRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_id: uuid.UUID
    collector: str
    collector_version: str
    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None
    error_type: str | None
    error_message: str | None
    stats: dict


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    type: EntityType
    display_name: str
    canonical_value: str
    aliases: list[str]
    attributes: dict
    confidence: float
    confidence_reasons: list[str]
    notes: str | None
    created_at: datetime
    source_finding_ids: list[uuid.UUID] = Field(default_factory=list)

    @classmethod
    def from_entity(cls, entity) -> EntityRead:
        return cls(
            id=entity.id,
            case_id=entity.case_id,
            type=entity.type,
            display_name=entity.display_name,
            canonical_value=entity.canonical_value,
            aliases=list(entity.aliases or []),
            attributes=dict(entity.attributes or {}),
            confidence=entity.confidence,
            confidence_reasons=list(entity.confidence_reasons or []),
            notes=entity.notes,
            created_at=entity.created_at,
            source_finding_ids=[finding.id for finding in (entity.sources or [])],
        )


class RelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID
    source_label: str | None = None
    target_label: str | None = None
    type: RelationshipType
    confidence: float
    confidence_reasons: list[str]
    strength: MatchStrength
    collector: str
    source_url: str | None
    attributes: dict
    evidence_finding_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_relationship(cls, relationship) -> RelationshipRead:
        return cls(
            id=relationship.id,
            case_id=relationship.case_id,
            source_entity_id=relationship.source_entity_id,
            target_entity_id=relationship.target_entity_id,
            source_label=(
                relationship.source_entity.display_name if relationship.source_entity else None
            ),
            target_label=(
                relationship.target_entity.display_name if relationship.target_entity else None
            ),
            type=relationship.type,
            confidence=relationship.confidence,
            confidence_reasons=list(relationship.confidence_reasons or []),
            strength=relationship.strength,
            collector=relationship.collector,
            source_url=relationship.source_url,
            attributes=dict(relationship.attributes or {}),
            evidence_finding_ids=[finding.id for finding in (relationship.evidence or [])],
            created_at=relationship.created_at,
        )


class TimelineEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    finding_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    occurred_at: datetime
    kind: str
    title: str
    description: str | None
    collector: str
    source_url: str | None
    confidence: float
    attributes: dict


class TimelineResponse(BaseModel):
    events: list[TimelineEventRead]
    summary: dict


class GraphNodeRead(BaseModel):
    id: str
    type: str
    label: str
    confidence: float
    attributes: dict


class GraphEdgeRead(BaseModel):
    id: str
    source: str
    target: str
    type: str
    confidence: float
    strength: str
    reasons: list[str]
    collector: str
    evidence_ids: list[str]
    attributes: dict


class GraphResponse(BaseModel):
    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]
    stats: dict
    summary: dict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    celery_id: str | None
    state: JobState
    progress: float
    message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_type: str | None
    error_message: str | None
    params: dict
    result: dict
    cancel_requested: bool
    created_at: datetime


class RunRequest(BaseModel):
    """Options for starting an investigation."""

    collectors: list[str] = Field(
        default_factory=list, description="Run only these collectors (by name)."
    )
    exclude_collectors: list[str] = Field(default_factory=list)
    target_ids: list[uuid.UUID] = Field(
        default_factory=list, description="Restrict the run to these targets."
    )


class RunResponse(BaseModel):
    job: JobRead
    dispatch: dict


class CollectorInfo(BaseModel):
    name: str
    version: str
    description: str
    supported_targets: list[str]
    requires_api_key: bool
    rate_limit: str
    timeout: float
    run_timeout: float | None
    source_attribution: str
    network: bool
    available: bool
    unavailable_reason: str
