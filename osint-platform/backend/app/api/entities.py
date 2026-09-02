"""Entity, relationship, graph and timeline endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CaseId, DbSession, parse_uuid
from app.graph import build_graph, graph_summary
from app.models import Entity, Relationship, TimelineEvent
from app.models.enums import EntityType, RelationshipType
from app.schemas.common import Page
from app.schemas.finding import (
    EntityRead,
    GraphResponse,
    RelationshipRead,
    TimelineEventRead,
    TimelineResponse,
)
from app.services import cases as case_service
from app.services.timeline import timeline_summary

router = APIRouter(prefix="/cases/{case_id}", tags=["entities"])


@router.get("/entities", response_model=Page[EntityRead], summary="List resolved entities")
def list_entities(
    case_id: CaseId,
    session: DbSession,
    entity_type: EntityType | None = Query(default=None, alias="type"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> Page[EntityRead]:
    case_service.get_case(session, case_id)
    stmt = select(Entity).where(Entity.case_id == case_id)
    count_stmt = select(func.count()).select_from(Entity).where(Entity.case_id == case_id)
    if entity_type is not None:
        stmt = stmt.where(Entity.type == entity_type)
        count_stmt = count_stmt.where(Entity.type == entity_type)
    if min_confidence > 0:
        stmt = stmt.where(Entity.confidence >= min_confidence)
        count_stmt = count_stmt.where(Entity.confidence >= min_confidence)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Entity.confidence.desc()).limit(limit).offset(offset))
    )
    return Page[EntityRead](
        items=[EntityRead.from_entity(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/entities/{entity_id}", response_model=EntityRead, summary="Fetch one entity")
def get_entity(case_id: CaseId, entity_id: str, session: DbSession) -> EntityRead:
    from app.core.errors import NotFoundError

    entity = session.get(Entity, parse_uuid(entity_id, "entity_id"))
    if entity is None or entity.case_id != case_id:
        raise NotFoundError(f"Entity {entity_id} does not exist in case {case_id}")
    return EntityRead.from_entity(entity)


@router.get(
    "/relationships",
    response_model=Page[RelationshipRead],
    summary="List relationships with their evidence",
)
def list_relationships(
    case_id: CaseId,
    session: DbSession,
    relationship_type: RelationshipType | None = Query(default=None, alias="type"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> Page[RelationshipRead]:
    case_service.get_case(session, case_id)
    stmt = select(Relationship).where(Relationship.case_id == case_id)
    count_stmt = (
        select(func.count()).select_from(Relationship).where(Relationship.case_id == case_id)
    )
    if relationship_type is not None:
        stmt = stmt.where(Relationship.type == relationship_type)
        count_stmt = count_stmt.where(Relationship.type == relationship_type)
    if min_confidence > 0:
        stmt = stmt.where(Relationship.confidence >= min_confidence)
        count_stmt = count_stmt.where(Relationship.confidence >= min_confidence)

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(stmt.order_by(Relationship.confidence.desc()).limit(limit).offset(offset))
    )
    return Page[RelationshipRead](
        items=[RelationshipRead.from_relationship(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/graph", response_model=GraphResponse, summary="Relationship graph")
def get_graph(
    case_id: CaseId,
    session: DbSession,
    min_confidence: float = Query(
        default=0.0, ge=0.0, le=1.0, description="Hide edges below this confidence."
    ),
    types: list[EntityType] | None = Query(default=None, description="Entity types to include."),
    relationship_types: list[RelationshipType] | None = Query(default=None),
) -> GraphResponse:
    """The graph as nodes and edges, ready for Cytoscape or React Flow."""
    case_service.get_case(session, case_id)
    entities = list(session.scalars(select(Entity).where(Entity.case_id == case_id)))
    relationships = list(
        session.scalars(select(Relationship).where(Relationship.case_id == case_id))
    )
    graph = build_graph(entities, relationships)
    if min_confidence > 0 or types or relationship_types:
        graph = graph.subgraph(
            min_confidence=min_confidence,
            entity_types=list(types) if types else None,
            relationship_types=list(relationship_types) if relationship_types else None,
        )
    exported = graph.to_dict()
    return GraphResponse(
        nodes=exported["nodes"],
        edges=exported["edges"],
        stats=exported["stats"],
        summary=graph_summary(graph),
    )


@router.get("/timeline", response_model=TimelineResponse, summary="Investigation timeline")
def get_timeline(
    case_id: CaseId,
    session: DbSession,
    kind: str | None = Query(default=None),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> TimelineResponse:
    case_service.get_case(session, case_id)
    stmt = select(TimelineEvent).where(TimelineEvent.case_id == case_id)
    if kind:
        stmt = stmt.where(TimelineEvent.kind == kind)
    stmt = stmt.order_by(
        TimelineEvent.occurred_at.asc() if order == "asc" else TimelineEvent.occurred_at.desc()
    )
    events = list(session.scalars(stmt))
    return TimelineResponse(
        events=[TimelineEventRead.model_validate(event) for event in events],
        summary=timeline_summary(events),
    )
