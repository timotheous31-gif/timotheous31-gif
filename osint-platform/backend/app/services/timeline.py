"""Timeline construction.

Turns dated findings into a chronological narrative. Only findings that carry a
real observation date contribute — a timeline built from "when we collected it"
rather than "when it happened" would be misleading.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.collection import Finding
from app.models.enums import FindingKind
from app.models.timeline import TimelineEvent

log = get_logger(__name__)

#: Finding kinds that carry a meaningful date, mapped to a timeline category.
TIMELINE_KINDS: dict[FindingKind, str] = {
    FindingKind.DOMAIN_REGISTRATION: "domain_registration",
    FindingKind.CERTIFICATE: "certificate",
    FindingKind.ARCHIVE_SNAPSHOT: "archive_snapshot",
    FindingKind.REPOSITORY: "repository_created",
    FindingKind.COMMIT_ACTIVITY: "commit_activity",
    FindingKind.CODE_PROFILE: "profile_created",
    FindingKind.EXPOSURE_SUMMARY: "exposure",
    FindingKind.SUBDOMAIN: "subdomain",
}


def build_timeline(session: Session, case_id: uuid.UUID) -> list[TimelineEvent]:
    """Rebuild the case timeline from its findings.

    Existing events are replaced so a re-run cannot accumulate duplicates.
    """
    findings = list(
        session.scalars(
            select(Finding).where(Finding.case_id == case_id, Finding.observed_at.is_not(None))
        )
    )
    existing = list(session.scalars(select(TimelineEvent).where(TimelineEvent.case_id == case_id)))
    for event in existing:
        session.delete(event)
    session.flush()

    events = _events_for(case_id, findings)
    session.add_all(events)
    session.flush()
    log.info("timeline.built", case_id=str(case_id), events=len(events))
    return sorted(events, key=lambda event: event.occurred_at)


def _events_for(case_id: uuid.UUID, findings: Iterable[Finding]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for finding in findings:
        occurred = _aware(finding.observed_at)
        if occurred is None:
            continue
        kind = TIMELINE_KINDS.get(finding.kind, str(finding.kind).lower())
        events.append(
            TimelineEvent(
                case_id=case_id,
                finding_id=finding.id,
                occurred_at=occurred,
                kind=kind,
                title=finding.title[:300],
                description=finding.summary,
                collector=finding.collector,
                source_url=finding.source_url,
                confidence=finding.confidence,
                attributes=_attributes(finding),
            )
        )
    return events


def _attributes(finding: Finding) -> dict[str, Any]:
    """A compact, already-filtered subset of the finding for the UI."""
    data = finding.data or {}
    keep = (
        "hostname",
        "domain",
        "handle",
        "common_name",
        "issuer",
        "full_name",
        "repository",
        "timestamp",
        "archived_url",
        "login",
        "breach_count",
    )
    return {key: data[key] for key in keep if key in data}


def _aware(value: datetime | None) -> datetime | None:
    """Normalise to timezone-aware UTC; SQLite returns naive datetimes."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def timeline_summary(events: list[TimelineEvent]) -> dict[str, Any]:
    """Span and composition of a timeline, for the case-overview page."""
    if not events:
        return {"event_count": 0, "first_event": None, "last_event": None, "kinds": {}}
    ordered = sorted(events, key=lambda event: _aware(event.occurred_at) or datetime.now(UTC))
    kinds: dict[str, int] = {}
    for event in ordered:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
    first = _aware(ordered[0].occurred_at)
    last = _aware(ordered[-1].occurred_at)
    return {
        "event_count": len(ordered),
        "first_event": first.isoformat() if first else None,
        "last_event": last.isoformat() if last else None,
        "span_days": (last - first).days if first and last else 0,
        "kinds": dict(sorted(kinds.items())),
    }
