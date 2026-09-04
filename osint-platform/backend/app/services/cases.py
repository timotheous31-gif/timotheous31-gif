"""Case and target service layer.

The API and the CLI both call these functions, so validation and normalisation
happen once, in one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import (
    Case,
    CaseStatus,
    CollectorRun,
    Entity,
    Evidence,
    Finding,
    Relationship,
    Tag,
    Target,
    TimelineEvent,
)
from app.models.enums import TargetStatus, TargetType
from app.schemas.case import CaseCreate, CaseUpdate, TargetCreate, TargetUpdate
from app.services.normalization import normalize_target

log = get_logger(__name__)


# ------------------------------------------------------------------------ tags


def get_or_create_tags(session: Session, names: Sequence[str]) -> list[Tag]:
    """Return :class:`Tag` rows for ``names``, creating any that are new."""
    cleaned = sorted({name.strip().lower() for name in names if name and name.strip()})
    if not cleaned:
        return []
    existing = list(session.scalars(select(Tag).where(Tag.name.in_(cleaned))))
    known = {tag.name for tag in existing}
    for name in cleaned:
        if name not in known:
            tag = Tag(name=name)
            session.add(tag)
            existing.append(tag)
    session.flush()
    return existing


# ----------------------------------------------------------------------- cases


def create_case(session: Session, payload: CaseCreate) -> Case:
    """Create an investigation."""
    case = Case(
        name=payload.name.strip(),
        description=payload.description,
        notes=payload.notes,
        status=CaseStatus.NEW,
        tags=get_or_create_tags(session, payload.tags),
    )
    session.add(case)
    session.flush()
    log.info("case.created", case_id=str(case.id), name=case.name)
    return case


def get_case(session: Session, case_id: uuid.UUID) -> Case:
    """Fetch a case or raise :class:`NotFoundError`."""
    case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} does not exist")
    return case


def list_cases(
    session: Session,
    *,
    status: CaseStatus | None = None,
    query: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Case], int]:
    """List cases with optional filters. Returns ``(items, total)``."""
    stmt = select(Case)
    count_stmt = select(func.count()).select_from(Case)

    if status is not None:
        stmt = stmt.where(Case.status == status)
        count_stmt = count_stmt.where(Case.status == status)
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(Case.name.ilike(pattern))
        count_stmt = count_stmt.where(Case.name.ilike(pattern))
    if tag:
        stmt = stmt.where(Case.tags.any(Tag.name == tag.strip().lower()))
        count_stmt = count_stmt.where(Case.tags.any(Tag.name == tag.strip().lower()))

    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(
            stmt.order_by(Case.created_at.desc()).limit(min(limit, 200)).offset(max(offset, 0))
        )
    )
    return items, total


def update_case(session: Session, case_id: uuid.UUID, payload: CaseUpdate) -> Case:
    """Apply a partial update to a case."""
    case = get_case(session, case_id)
    data = payload.model_dump(exclude_unset=True)
    if "tags" in data and data["tags"] is not None:
        case.tags = get_or_create_tags(session, data.pop("tags"))
    else:
        data.pop("tags", None)
    for field, value in data.items():
        setattr(case, field, value)
    session.flush()
    log.info("case.updated", case_id=str(case.id), fields=sorted(data))
    return case


def delete_case(session: Session, case_id: uuid.UUID) -> None:
    """Delete a case and everything it contains."""
    case = get_case(session, case_id)
    session.delete(case)
    session.flush()
    log.info("case.deleted", case_id=str(case_id))


def case_summary(session: Session, case_id: uuid.UUID) -> dict[str, object]:
    """Counters for the case-overview page."""
    case = get_case(session, case_id)

    def _count(model: type, column: InstrumentedAttribute[uuid.UUID]) -> int:
        stmt = select(func.count()).select_from(model).where(column == case_id)
        return session.scalar(stmt) or 0

    collectors = sorted(
        set(
            session.scalars(
                select(CollectorRun.collector).where(CollectorRun.case_id == case_id).distinct()
            )
        )
    )
    last_run = session.scalar(
        select(func.max(CollectorRun.finished_at)).where(CollectorRun.case_id == case_id)
    )

    buckets = {"high": 0, "medium": 0, "low": 0}
    for confidence in session.scalars(select(Finding.confidence).where(Finding.case_id == case_id)):
        if confidence >= 0.8:
            buckets["high"] += 1
        elif confidence >= 0.5:
            buckets["medium"] += 1
        else:
            buckets["low"] += 1

    return {
        "case": case,
        "targets": _count(Target, Target.case_id),
        "findings": _count(Finding, Finding.case_id),
        "entities": _count(Entity, Entity.case_id),
        "relationships": _count(Relationship, Relationship.case_id),
        "evidence": _count(Evidence, Evidence.case_id),
        "timeline_events": _count(TimelineEvent, TimelineEvent.case_id),
        "collectors_run": collectors,
        "confidence_distribution": buckets,
        "last_run_at": last_run,
    }


# --------------------------------------------------------------------- targets


def add_target(session: Session, case_id: uuid.UUID, payload: TargetCreate) -> Target:
    """Normalise and attach a target to a case.

    Raises:
        ValidationError: the value is not a valid target of its type.
        ConflictError: the same normalised target is already in the case.
    """
    case = get_case(session, case_id)
    normalized = normalize_target(payload.value, payload.type)

    existing = session.scalar(
        select(Target).where(
            Target.case_id == case.id,
            Target.type == normalized.type,
            Target.normalized_value == normalized.value,
        )
    )
    if existing is not None:
        raise ConflictError(
            f"{normalized.type} target {normalized.value!r} is already in this case",
            detail={"target_id": str(existing.id)},
        )

    target = Target(
        case_id=case.id,
        type=normalized.type,
        raw_input=normalized.raw_input,
        normalized_value=normalized.value,
        attributes=normalized.attributes,
        notes=payload.notes,
        status=TargetStatus.PENDING,
        tags=get_or_create_tags(session, payload.tags),
    )
    session.add(target)
    session.flush()
    log.info(
        "target.added",
        case_id=str(case.id),
        target_id=str(target.id),
        target_type=str(normalized.type),
    )
    return target


def get_target(session: Session, case_id: uuid.UUID, target_id: uuid.UUID) -> Target:
    target = session.get(Target, target_id)
    if target is None or target.case_id != case_id:
        raise NotFoundError(f"Target {target_id} does not exist in case {case_id}")
    return target


def list_targets(
    session: Session,
    case_id: uuid.UUID,
    *,
    target_type: TargetType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Target], int]:
    get_case(session, case_id)
    stmt = select(Target).where(Target.case_id == case_id)
    count_stmt = select(func.count()).select_from(Target).where(Target.case_id == case_id)
    if target_type is not None:
        stmt = stmt.where(Target.type == target_type)
        count_stmt = count_stmt.where(Target.type == target_type)
    total = session.scalar(count_stmt) or 0
    items = list(
        session.scalars(
            stmt.order_by(Target.created_at.asc()).limit(min(limit, 500)).offset(max(offset, 0))
        )
    )
    return items, total


def update_target(
    session: Session, case_id: uuid.UUID, target_id: uuid.UUID, payload: TargetUpdate
) -> Target:
    target = get_target(session, case_id, target_id)
    data = payload.model_dump(exclude_unset=True)
    if "tags" in data and data["tags"] is not None:
        target.tags = get_or_create_tags(session, data.pop("tags"))
    else:
        data.pop("tags", None)
    for field, value in data.items():
        setattr(target, field, value)
    session.flush()
    return target


def delete_target(session: Session, case_id: uuid.UUID, target_id: uuid.UUID) -> None:
    target = get_target(session, case_id, target_id)
    session.delete(target)
    session.flush()
    log.info("target.deleted", case_id=str(case_id), target_id=str(target_id))
