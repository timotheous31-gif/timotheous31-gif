"""Analyst decisions.

A decision is what a human concluded. Automated confidence is what the platform
computed. They are different claims with different authority, and a report has
to be able to show both — so recording a decision writes only to
``analyst_decisions`` and never touches the row it judges.

That separation is enforced by the schema rather than by discipline: this module
has no write path to a candidate's confidence, a profile's confidence, or an
image's provenance, and a test asserts those values are byte-identical before
and after a decision is recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models import (
    AnalystDecision,
    AnalystDecisionRecord,
    Case,
    DecisionSubject,
    Entity,
    ImageEvidence,
    SocialProfile,
)

log = get_logger(__name__)

#: How to find each kind of subject, so a decision cannot be recorded against
#: something that does not exist or belongs to another case.
_SUBJECT_MODELS: dict[DecisionSubject, type[Any]] = {
    DecisionSubject.CANDIDATE: Entity,
    DecisionSubject.SOCIAL_PROFILE: SocialProfile,
    DecisionSubject.IMAGE: ImageEvidence,
}


def record_decision(
    session: Session,
    *,
    case_id: uuid.UUID,
    subject_type: DecisionSubject,
    subject_id: uuid.UUID,
    decision: AnalystDecision,
    note: str | None = None,
    decided_by: str | None = None,
) -> AnalystDecisionRecord:
    """Record (or update) the analyst's judgement about one association.

    Raises:
        NotFoundError: the case does not exist.
        ValidationError: the subject does not exist, or is not in this case.
    """
    if session.get(Case, case_id) is None:
        raise NotFoundError(f"Case {case_id} does not exist")

    model = _SUBJECT_MODELS[subject_type]
    subject = session.get(model, subject_id)
    # getattr rather than subject.case_id: the mapping is over three unrelated
    # models, so mypy sees only their common Base here.
    if subject is None or getattr(subject, "case_id", None) != case_id:
        raise ValidationError(
            f"No {subject_type} {subject_id} in this case",
            detail={"subject_type": str(subject_type), "subject_id": str(subject_id)},
        )

    existing = session.scalar(
        select(AnalystDecisionRecord).where(
            AnalystDecisionRecord.case_id == case_id,
            AnalystDecisionRecord.subject_type == subject_type,
            AnalystDecisionRecord.subject_id == subject_id,
        )
    )
    moment = datetime.now(UTC)
    if existing is not None:
        existing.decision = decision
        existing.note = note
        existing.decided_by = decided_by
        existing.decided_at = moment
        session.flush()
        record = existing
    else:
        record = AnalystDecisionRecord(
            case_id=case_id,
            subject_type=subject_type,
            subject_id=subject_id,
            decision=decision,
            note=note,
            decided_by=decided_by,
            decided_at=moment,
        )
        session.add(record)
        session.flush()

    log.info(
        "analyst.decision_recorded",
        case_id=str(case_id),
        subject_type=str(subject_type),
        decision=str(decision),
    )
    return record


def decisions_for_case(
    session: Session, case_id: uuid.UUID, *, subject_type: DecisionSubject | None = None
) -> list[AnalystDecisionRecord]:
    query = select(AnalystDecisionRecord).where(AnalystDecisionRecord.case_id == case_id)
    if subject_type is not None:
        query = query.where(AnalystDecisionRecord.subject_type == subject_type)
    return list(session.scalars(query.order_by(AnalystDecisionRecord.decided_at.desc())))


def decision_map(
    session: Session, case_id: uuid.UUID, subject_ids: Sequence[uuid.UUID] | None = None
) -> dict[str, AnalystDecisionRecord]:
    """Decisions keyed by subject id, for rendering a list in one query."""
    query = select(AnalystDecisionRecord).where(AnalystDecisionRecord.case_id == case_id)
    if subject_ids:
        query = query.where(AnalystDecisionRecord.subject_id.in_(list(subject_ids)))
    return {str(record.subject_id): record for record in session.scalars(query)}


def clear_decision(
    session: Session, *, case_id: uuid.UUID, subject_type: DecisionSubject, subject_id: uuid.UUID
) -> bool:
    """Remove a decision, returning whether one was there.

    Removing the analyst's view leaves the automated assessment exactly as it
    was — there is nothing to restore, because nothing was overwritten.
    """
    existing = session.scalar(
        select(AnalystDecisionRecord).where(
            AnalystDecisionRecord.case_id == case_id,
            AnalystDecisionRecord.subject_type == subject_type,
            AnalystDecisionRecord.subject_id == subject_id,
        )
    )
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True
