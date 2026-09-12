"""Recording what one execution observed, in the state it observed it.

The problem, stated as a reproducibility failure: a case's findings, entities,
profiles, contacts, images and evidence are canonical and mutable. A finding is
deduplicated across the case, so a rerun returns the existing row and its
``run_id`` still names the first run that ever produced it. Generating "the report
for execution 1" after execution 2 therefore showed execution 2's evidence,
anchors and scores as though execution 1 had known them.

This module writes the ledger that fixes it. For every object an execution
touches it records one :class:`~app.models.ExecutionObservation`: which execution,
which stage, which collector, when, whether this execution created the object —
and a **snapshot** of the state the object was in when the execution finished.

Two decisions are worth stating plainly.

**The snapshot is what a historical report renders.** Not the canonical row. A
report built for execution 1 reads execution 1's snapshots, so a later execution
changing a score, a reason or a payload cannot reach backwards into it.

**Nothing is backfilled.** Rows that predate this ledger get no observations, and
a report asked for such an execution says it has no ledger rather than implying
the execution found nothing. Fabricating an execution for legacy rows would be
the same class of lie the ledger exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Entity,
    Evidence,
    ExecutionObservation,
    Finding,
    ImageEvidence,
    PublicContact,
    Relationship,
    SocialProfile,
)
from app.models.enums import ObservationStage, ObservationSubject

#: Written onto a job's ``result`` when an execution recorded a ledger. Its
#: absence is how a legacy execution is told apart from one that observed nothing,
#: which are different facts and must not render the same way.
LEDGER_FLAG = "observations_recorded"

#: The model behind each subject kind, so one function can snapshot any of them.
SUBJECT_MODELS: dict[ObservationSubject, type[Any]] = {
    ObservationSubject.FINDING: Finding,
    ObservationSubject.EVIDENCE: Evidence,
    ObservationSubject.ENTITY: Entity,
    ObservationSubject.RELATIONSHIP: Relationship,
    ObservationSubject.SOCIAL_PROFILE: SocialProfile,
    ObservationSubject.PUBLIC_CONTACT: PublicContact,
    ObservationSubject.IMAGE_EVIDENCE: ImageEvidence,
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def snapshot(subject_type: ObservationSubject, row: Any) -> dict[str, Any]:
    """The state of one object, as much of it as a report needs to render.

    Deliberately a projection and not a full serialisation: an execution report
    must be able to show a finding exactly as that execution left it, including
    its score, its reasons and its payload, and nothing beyond that is worth the
    storage. Every projection includes ``id`` so a snapshot can be matched back
    to the canonical row a reader may want to inspect as it is *now*.
    """
    if subject_type is ObservationSubject.FINDING:
        return {
            "id": str(row.id),
            "kind": str(row.kind),
            "title": row.title,
            "summary": row.summary,
            "collector": row.collector,
            "source_url": row.source_url,
            "confidence": round(float(row.confidence), 6),
            "confidence_reasons": list(row.confidence_reasons or []),
            "classification": str(row.classification),
            "redacted": bool(row.redacted),
            "dedupe_key": row.dedupe_key,
            "observed_at": _iso(row.observed_at),
            "target_id": str(row.target_id) if row.target_id else None,
            # The payload as this execution left it. A later execution
            # rescoring the same page rewrites the canonical row's data; this
            # copy is what keeps the older report truthful.
            "data": dict(row.data or {}),
            "evidence_sha256": sorted(item.sha256 for item in (row.evidence or [])),
        }
    if subject_type is ObservationSubject.EVIDENCE:
        return {
            "id": str(row.id),
            "collector": row.collector,
            "source_url": row.source_url,
            "sha256": row.sha256,
            "content_type": row.content_type,
            "size_bytes": int(row.size_bytes or 0),
            "retrieved_at": _iso(row.retrieved_at),
            "redacted": bool(row.redacted),
        }
    if subject_type is ObservationSubject.ENTITY:
        return {
            "id": str(row.id),
            "type": str(row.type),
            "display_name": row.display_name,
            "canonical_value": row.canonical_value,
            "confidence": round(float(row.confidence), 6),
            "confidence_reasons": list(row.confidence_reasons or []),
            "attributes": dict(row.attributes or {}),
        }
    if subject_type is ObservationSubject.RELATIONSHIP:
        return {
            "id": str(row.id),
            "type": str(row.type),
            "source_entity_id": str(row.source_entity_id),
            "target_entity_id": str(row.target_entity_id),
            "confidence": round(float(row.confidence), 6),
            "confidence_reasons": list(row.confidence_reasons or []),
            "strength": str(row.strength),
            "collector": row.collector,
            "attributes": dict(row.attributes or {}),
        }
    if subject_type is ObservationSubject.SOCIAL_PROFILE:
        return {
            "id": str(row.id),
            "platform": row.platform,
            "platform_label": row.platform_label,
            "handle": row.handle,
            "profile_url": row.profile_url,
            "display_name": row.display_name,
            "bio": row.bio,
            "accessibility": str(row.accessibility),
            "server_fetchable": bool(row.server_fetchable),
            "fetch_note": row.fetch_note,
            "collector": row.collector,
            "evidence_class": row.evidence_class,
            "confidence": round(float(row.confidence), 6),
            "match_reasons": list(row.match_reasons or []),
            "mismatch_reasons": list(row.mismatch_reasons or []),
            "corroborated_by": list(row.corroborated_by or []),
            "retrieved_at": _iso(row.retrieved_at),
            "candidate_entity_id": (
                str(row.candidate_entity_id) if row.candidate_entity_id else None
            ),
            "attributes": dict(row.attributes or {}),
        }
    if subject_type is ObservationSubject.PUBLIC_CONTACT:
        return {
            "id": str(row.id),
            "contact_type": str(row.contact_type),
            "value": row.value,
            "label": row.label,
            "classification": str(row.classification),
            "source_url": row.source_url,
            "source_name": row.source_name,
            "collector": row.collector,
            "evidence_class": row.evidence_class,
            "confidence": round(float(row.confidence), 6),
            "confidence_reasons": list(row.confidence_reasons or []),
            "extraction_reason": row.extraction_reason,
            "retrieved_at": _iso(row.retrieved_at),
            "candidate_entity_id": (
                str(row.candidate_entity_id) if row.candidate_entity_id else None
            ),
        }
    if subject_type is ObservationSubject.IMAGE_EVIDENCE:
        return {
            "id": str(row.id),
            "image_url": row.image_url,
            "source_page_url": row.source_page_url,
            "platform": row.platform,
            "caption": row.caption,
            "context_text": row.context_text,
            "fetch_state": str(row.fetch_state),
            "sha256": row.sha256,
            "content_type": row.content_type,
            "byte_length": row.byte_length,
            "width": row.width,
            "height": row.height,
            "final_url": row.final_url,
            "fetch_note": row.fetch_note,
            "origin": row.origin,
            "evidence_class": row.evidence_class,
            "retrieved_at": _iso(row.retrieved_at),
            "candidate_entity_id": (
                str(row.candidate_entity_id) if row.candidate_entity_id else None
            ),
        }
    return {"id": str(row.id)}


class ExecutionLedger:
    """The objects one execution touched, and the stage that touched each.

    Accumulated during the run and written once at the end, because the state
    worth snapshotting is the state the execution *left* — a profile promoted in
    one stage and rescored by a later one has a single truthful snapshot, not two
    that disagree.
    """

    __slots__ = ("_touched",)

    def __init__(self) -> None:
        # (subject_type, subject_id) -> observation fields
        self._touched: dict[tuple[ObservationSubject, uuid.UUID], dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._touched)

    def touch(
        self,
        subject_type: ObservationSubject,
        subject_id: uuid.UUID,
        *,
        stage: ObservationStage,
        collector: str,
        run_id: uuid.UUID | None = None,
        source_url: str | None = None,
        observed_at: datetime | None = None,
        first_seen: bool = False,
    ) -> None:
        """Note that this execution observed one object.

        Touching the same object twice keeps the *earliest* stage that saw it and
        promotes ``first_seen`` if any touch created it: a finding retrieved by a
        collector and then re-observed by promotion was retrieved, not promoted.
        """
        key = (subject_type, subject_id)
        existing = self._touched.get(key)
        if existing is None:
            self._touched[key] = {
                "stage": stage,
                "collector": collector,
                "run_id": run_id,
                "source_url": source_url,
                "observed_at": observed_at or datetime.now(UTC),
                "first_seen": first_seen,
            }
            return
        existing["first_seen"] = existing["first_seen"] or first_seen
        if existing["run_id"] is None and run_id is not None:
            existing["run_id"] = run_id
        if existing["source_url"] is None and source_url is not None:
            existing["source_url"] = source_url

    def subjects(self) -> Iterable[tuple[ObservationSubject, uuid.UUID, dict[str, Any]]]:
        for (subject_type, subject_id), fields in self._touched.items():
            yield subject_type, subject_id, fields


def write_ledger(
    session: Session,
    *,
    case_id: uuid.UUID,
    job_id: uuid.UUID | None,
    ledger: ExecutionLedger,
) -> int:
    """Persist one execution's observations, snapshotting each subject now.

    Returns how many rows were written. A subject whose canonical row has since
    been deleted is skipped rather than recorded as an empty snapshot: the ledger
    records what was observed, and an object that no longer exists cannot be
    re-read to say what state it was in.
    """
    if job_id is None:
        # No tracked execution, so there is nothing for an observation to belong
        # to. A row keyed on NULL would be an execution-shaped record of no
        # execution: unreachable by any execution report and overwritten by the
        # next unattributed run. The honest answer is to record nothing and let
        # the report say this execution has no ledger.
        return 0
    written = 0
    for subject_type, subject_id, fields in ledger.subjects():
        model = SUBJECT_MODELS.get(subject_type)
        if model is None:
            continue
        row = session.get(model, subject_id)
        if row is None:
            continue
        existing = session.scalar(
            select(ExecutionObservation).where(
                ExecutionObservation.job_id == job_id,
                ExecutionObservation.subject_type == subject_type,
                ExecutionObservation.subject_id == subject_id,
            )
        )
        state = snapshot(subject_type, row)
        if existing is not None:
            # One row per execution per subject. Re-running *the same* execution
            # (a retried job) refreshes its own row; it never edits another
            # execution's.
            existing.state = state
            existing.observed_at = fields["observed_at"]
            existing.first_seen = existing.first_seen or fields["first_seen"]
            continue
        session.add(
            ExecutionObservation(
                case_id=case_id,
                job_id=job_id,
                run_id=fields["run_id"],
                stage=fields["stage"],
                subject_type=subject_type,
                subject_id=subject_id,
                collector=fields["collector"],
                source_url=fields["source_url"],
                observed_at=fields["observed_at"],
                first_seen=fields["first_seen"],
                state=state,
            )
        )
        written += 1
    session.flush()
    return written


def observations_for(
    session: Session,
    case_id: uuid.UUID,
    job_id: uuid.UUID,
    subject_type: ObservationSubject | None = None,
) -> list[ExecutionObservation]:
    """Everything one execution observed, oldest first."""
    stmt = select(ExecutionObservation).where(
        ExecutionObservation.case_id == case_id,
        ExecutionObservation.job_id == job_id,
    )
    if subject_type is not None:
        stmt = stmt.where(ExecutionObservation.subject_type == subject_type)
    return list(session.scalars(stmt.order_by(ExecutionObservation.observed_at)))


def has_ledger(session: Session, case_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Whether this execution recorded observations at all.

    False for an execution that ran before the ledger existed *and* for one that
    genuinely observed nothing. The two are told apart by :data:`LEDGER_FLAG` on
    the job's result, which only a ledger-aware execution writes.
    """
    return (
        session.scalar(
            select(ExecutionObservation.id)
            .where(
                ExecutionObservation.case_id == case_id,
                ExecutionObservation.job_id == job_id,
            )
            .limit(1)
        )
        is not None
    )


def first_seen_execution(
    session: Session, case_id: uuid.UUID, subject_type: ObservationSubject, subject_id: uuid.UUID
) -> uuid.UUID | None:
    """The execution that created this object, if the ledger recorded one."""
    return session.scalar(
        select(ExecutionObservation.job_id)
        .where(
            ExecutionObservation.case_id == case_id,
            ExecutionObservation.subject_type == subject_type,
            ExecutionObservation.subject_id == subject_id,
            ExecutionObservation.first_seen.is_(True),
        )
        .order_by(ExecutionObservation.observed_at)
        .limit(1)
    )


def last_seen_execution(
    session: Session, case_id: uuid.UUID, subject_type: ObservationSubject, subject_id: uuid.UUID
) -> uuid.UUID | None:
    """The most recent execution that observed this object."""
    return session.scalar(
        select(ExecutionObservation.job_id)
        .where(
            ExecutionObservation.case_id == case_id,
            ExecutionObservation.subject_type == subject_type,
            ExecutionObservation.subject_id == subject_id,
        )
        .order_by(ExecutionObservation.observed_at.desc())
        .limit(1)
    )


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None


def _uuid(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:  # pragma: no cover - defensive
        return None


def rehydrate(observation: ExecutionObservation) -> Any:
    """A transient model instance carrying the snapshotted state.

    Deliberately a real model object and deliberately *not* added to the session.
    It lets the report's existing item builders render a historical execution
    without a second set of builders that read dicts — a second implementation is
    how two renderings of the same evidence come to disagree, which this codebase
    has paid for more than once.

    The object is detached and unsaveable on purpose: it is a photograph of a row,
    not the row. Nothing may write it back.
    """
    from app.models.enums import (
        Classification,
        ContactClassification,
        ContactType,
        EntityType,
        FindingKind,
        ImageFetchState,
        MatchStrength,
        ProfileAccess,
        RelationshipType,
    )

    state = dict(observation.state or {})
    kind = observation.subject_type
    if kind is ObservationSubject.FINDING:
        return Finding(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            target_id=_uuid(state.get("target_id")),
            run_id=observation.run_id,
            kind=FindingKind(str(state.get("kind") or FindingKind.SEARCH_RESULT)),
            title=str(state.get("title") or ""),
            summary=state.get("summary"),
            data=dict(state.get("data") or {}),
            collector=str(state.get("collector") or observation.collector),
            source_url=state.get("source_url"),
            confidence=float(state.get("confidence") or 0.0),
            confidence_reasons=list(state.get("confidence_reasons") or []),
            classification=Classification(
                str(state.get("classification") or Classification.PUBLIC)
            ),
            redacted=bool(state.get("redacted")),
            dedupe_key=str(state.get("dedupe_key") or ""),
            observed_at=_dt(state.get("observed_at")),
        )
    if kind is ObservationSubject.EVIDENCE:
        return Evidence(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            collector=str(state.get("collector") or observation.collector),
            source_url=state.get("source_url"),
            retrieved_at=_dt(state.get("retrieved_at")) or observation.observed_at,
            sha256=str(state.get("sha256") or ""),
            content_type=state.get("content_type"),
            size_bytes=int(state.get("size_bytes") or 0),
            redacted=bool(state.get("redacted")),
        )
    if kind is ObservationSubject.ENTITY:
        return Entity(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            type=EntityType(str(state.get("type") or EntityType.PERSONA)),
            display_name=str(state.get("display_name") or ""),
            canonical_value=str(state.get("canonical_value") or ""),
            confidence=float(state.get("confidence") or 0.0),
            confidence_reasons=list(state.get("confidence_reasons") or []),
            attributes=dict(state.get("attributes") or {}),
        )
    if kind is ObservationSubject.RELATIONSHIP:
        return Relationship(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            source_entity_id=_uuid(state.get("source_entity_id")),
            target_entity_id=_uuid(state.get("target_entity_id")),
            type=RelationshipType(str(state.get("type") or RelationshipType.MENTIONS)),
            confidence=float(state.get("confidence") or 0.0),
            confidence_reasons=list(state.get("confidence_reasons") or []),
            strength=MatchStrength(str(state.get("strength") or MatchStrength.WEAK_ASSOCIATION)),
            collector=str(state.get("collector") or observation.collector),
            attributes=dict(state.get("attributes") or {}),
        )
    if kind is ObservationSubject.SOCIAL_PROFILE:
        return SocialProfile(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            candidate_entity_id=_uuid(state.get("candidate_entity_id")),
            platform=str(state.get("platform") or ""),
            platform_label=str(state.get("platform_label") or ""),
            handle=state.get("handle"),
            profile_url=str(state.get("profile_url") or ""),
            display_name=state.get("display_name"),
            bio=state.get("bio"),
            source_url=state.get("source_url"),
            accessibility=ProfileAccess(str(state.get("accessibility") or ProfileAccess.UNKNOWN)),
            server_fetchable=bool(state.get("server_fetchable")),
            fetch_note=state.get("fetch_note"),
            collector=str(state.get("collector") or observation.collector),
            evidence_class=str(state.get("evidence_class") or ""),
            confidence=float(state.get("confidence") or 0.0),
            match_reasons=list(state.get("match_reasons") or []),
            mismatch_reasons=list(state.get("mismatch_reasons") or []),
            corroborated_by=list(state.get("corroborated_by") or []),
            retrieved_at=_dt(state.get("retrieved_at")),
            attributes=dict(state.get("attributes") or {}),
        )
    if kind is ObservationSubject.PUBLIC_CONTACT:
        return PublicContact(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            candidate_entity_id=_uuid(state.get("candidate_entity_id")),
            contact_type=ContactType(str(state.get("contact_type") or ContactType.WEBSITE)),
            value=str(state.get("value") or ""),
            label=state.get("label"),
            classification=ContactClassification(
                str(
                    state.get("classification") or ContactClassification.UNVERIFIED_PUBLIC_REFERENCE
                )
            ),
            source_url=state.get("source_url"),
            source_name=str(state.get("source_name") or ""),
            collector=str(state.get("collector") or observation.collector),
            evidence_class=str(state.get("evidence_class") or ""),
            confidence=float(state.get("confidence") or 0.0),
            confidence_reasons=list(state.get("confidence_reasons") or []),
            extraction_reason=state.get("extraction_reason"),
            retrieved_at=_dt(state.get("retrieved_at")),
        )
    if kind is ObservationSubject.IMAGE_EVIDENCE:
        return ImageEvidence(
            id=_uuid(state.get("id")),
            case_id=observation.case_id,
            candidate_entity_id=_uuid(state.get("candidate_entity_id")),
            image_url=str(state.get("image_url") or ""),
            source_page_url=str(state.get("source_page_url") or ""),
            platform=state.get("platform"),
            caption=state.get("caption"),
            context_text=state.get("context_text"),
            fetch_state=ImageFetchState(state.get("fetch_state", ImageFetchState.REFERENCE_ONLY)),
            sha256=state.get("sha256"),
            content_type=state.get("content_type"),
            byte_length=state.get("byte_length"),
            width=state.get("width"),
            height=state.get("height"),
            final_url=state.get("final_url"),
            fetch_note=state.get("fetch_note"),
            origin=str(state.get("origin") or observation.collector),
            evidence_class=str(state.get("evidence_class") or ""),
            retrieved_at=_dt(state.get("retrieved_at")),
        )
    raise ValueError(f"Cannot rehydrate {kind}")


@dataclass(slots=True)
class ExecutionSnapshot:
    """Everything one execution observed, rehydrated and grouped by kind.

    ``recorded`` is False when no ledger exists for the execution. A report must
    then say so rather than rendering an empty case, because "this execution
    predates the observation ledger" and "this execution found nothing" are
    different claims and only one of them is about the subject.
    """

    job_id: uuid.UUID
    recorded: bool = False
    findings: list[Any] = dataclass_field(default_factory=list)
    evidence: list[Any] = dataclass_field(default_factory=list)
    entities: list[Any] = dataclass_field(default_factory=list)
    relationships: list[Any] = dataclass_field(default_factory=list)
    profiles: list[Any] = dataclass_field(default_factory=list)
    contacts: list[Any] = dataclass_field(default_factory=list)
    images: list[Any] = dataclass_field(default_factory=list)
    #: subject id -> the stage that observed it, for the report's provenance.
    stages: dict[str, str] = dataclass_field(default_factory=dict)
    #: Subject ids this execution was the first to see.
    first_seen: set[str] = dataclass_field(default_factory=set)

    @property
    def observation_count(self) -> int:
        return (
            len(self.findings)
            + len(self.evidence)
            + len(self.entities)
            + len(self.relationships)
            + len(self.profiles)
            + len(self.contacts)
            + len(self.images)
        )


def execution_snapshot(
    session: Session, case_id: uuid.UUID, job_id: uuid.UUID
) -> ExecutionSnapshot:
    """Rehydrate one execution's ledger into renderable rows."""
    rows = observations_for(session, case_id, job_id)
    snapshot_out = ExecutionSnapshot(job_id=job_id, recorded=bool(rows))
    buckets: dict[ObservationSubject, list[Any]] = {
        ObservationSubject.FINDING: snapshot_out.findings,
        ObservationSubject.EVIDENCE: snapshot_out.evidence,
        ObservationSubject.ENTITY: snapshot_out.entities,
        ObservationSubject.RELATIONSHIP: snapshot_out.relationships,
        ObservationSubject.SOCIAL_PROFILE: snapshot_out.profiles,
        ObservationSubject.PUBLIC_CONTACT: snapshot_out.contacts,
        ObservationSubject.IMAGE_EVIDENCE: snapshot_out.images,
    }
    for row in rows:
        bucket = buckets.get(row.subject_type)
        if bucket is None:
            continue
        bucket.append(rehydrate(row))
        snapshot_out.stages[str(row.subject_id)] = str(row.stage)
        if row.first_seen:
            snapshot_out.first_seen.add(str(row.subject_id))

    # Re-attach each finding to the artefacts that support it. A rehydrated
    # finding has an empty relationship collection, so without this an execution
    # report printed "no stored artefact is attached to this finding" against
    # every finding — a false statement, and the worst possible one in a report
    # whose whole claim is that every assertion is traceable to a hash.
    by_hash = {item.sha256: item for item in snapshot_out.evidence if item.sha256}
    for observation, finding in zip(
        [item for item in rows if item.subject_type is ObservationSubject.FINDING],
        snapshot_out.findings,
        strict=False,
    ):
        hashes = (observation.state or {}).get("evidence_sha256") or []
        finding.evidence = [by_hash[str(value)] for value in hashes if str(value) in by_hash]
    return snapshot_out
