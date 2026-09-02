"""The investigation engine.

One place where the whole pipeline is expressed, so the CLI, the API and the
Celery worker all run exactly the same investigation:

    plan → collect → normalise → privacy filter → persist findings + evidence
         → extract entities → correlate → score → timeline → summary

Each stage is a separate method so it can be tested on its own, and each is
resilient: a failing collector produces a FAILED run row and the investigation
continues; a failing *stage* is recorded on the job and does not corrupt what
earlier stages already wrote.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.base import CollectorContext, FindingDraft, RawPayload
from app.collectors.registry import load_builtin_collectors, plan_collectors
from app.collectors.runner import RunOutcome, run_all
from app.core.errors import NotFoundError
from app.core.logging import case_id_var, get_logger, target_id_var
from app.core.settings import Settings, get_settings
from app.correlation.extraction import extract
from app.correlation.resolver import EntityResolver, ResolutionSummary
from app.models import (
    Case,
    CaseStatus,
    CollectorRun,
    Finding,
    Job,
    RunStatus,
    Target,
    TargetStatus,
)
from app.models.enums import JobState
from app.privacy.filter import PrivacyFilter
from app.services.evidence import EvidenceStore
from app.services.normalization import NormalizedTarget, normalize_target
from app.services.timeline import build_timeline

log = get_logger(__name__)

#: Called with a fraction (0..1) and a human-readable message.
ProgressHook = Callable[[float, str], None]


@dataclass(slots=True)
class InvestigationOptions:
    """What to run, and how."""

    include_collectors: list[str] = field(default_factory=list)
    exclude_collectors: list[str] = field(default_factory=list)
    target_ids: list[uuid.UUID] = field(default_factory=list)
    #: Polled between collectors so a queued cancellation takes effect quickly.
    should_cancel: Callable[[], bool] | None = None
    on_progress: ProgressHook | None = None


@dataclass(slots=True)
class InvestigationResult:
    """Everything one investigation produced, for the job record and the CLI."""

    case_id: uuid.UUID
    targets: int = 0
    collectors_run: int = 0
    collectors_failed: int = 0
    collectors_skipped: int = 0
    findings_created: int = 0
    findings_duplicate: int = 0
    evidence_stored: int = 0
    entities_created: int = 0
    entities_updated: int = 0
    relationships_created: int = 0
    inferred_links: int = 0
    timeline_events: int = 0
    cancelled: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": str(self.case_id),
            "targets": self.targets,
            "collectors_run": self.collectors_run,
            "collectors_failed": self.collectors_failed,
            "collectors_skipped": self.collectors_skipped,
            "findings_created": self.findings_created,
            "findings_duplicate": self.findings_duplicate,
            "evidence_stored": self.evidence_stored,
            "entities_created": self.entities_created,
            "entities_updated": self.entities_updated,
            "relationships_created": self.relationships_created,
            "inferred_links": self.inferred_links,
            "timeline_events": self.timeline_events,
            "cancelled": self.cancelled,
            "errors": self.errors,
            "notes": self.notes,
        }


class InvestigationEngine:
    """Runs an investigation for a case."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        privacy: PrivacyFilter | None = None,
        evidence: EvidenceStore | None = None,
        resolver: EntityResolver | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.privacy = privacy or PrivacyFilter(self.settings)
        self.evidence = evidence or EvidenceStore(self.settings, self.privacy)
        self.resolver = resolver or EntityResolver()
        load_builtin_collectors()

    # ------------------------------------------------------------- entry

    def run(
        self,
        session: Session,
        case_id: uuid.UUID,
        options: InvestigationOptions | None = None,
        *,
        job: Job | None = None,
    ) -> InvestigationResult:
        """Run the full pipeline synchronously (the worker calls this)."""
        options = options or InvestigationOptions()
        case = session.get(Case, case_id)
        if case is None:
            raise NotFoundError(f"Case {case_id} does not exist")

        token = case_id_var.set(str(case_id))
        result = InvestigationResult(case_id=case_id)
        case.status = CaseStatus.RUNNING
        session.flush()

        try:
            targets = self._targets(session, case_id, options)
            result.targets = len(targets)
            if not targets:
                result.notes.append("The case has no targets to investigate")
                case.status = CaseStatus.COMPLETE
                session.flush()
                return result

            for index, target in enumerate(targets):
                if options.should_cancel is not None and options.should_cancel():
                    result.cancelled = True
                    result.notes.append("Investigation cancelled by request")
                    break
                self._investigate_target(session, case_id, target, options, result, job)
                self._report(
                    options,
                    (index + 1) / (len(targets) + 1),
                    f"Collected {target.type} {target.normalized_value}",
                )

            self._correlate(session, case_id, result)
            self._report(options, 0.95, "Correlating entities")
            self._build_timeline(session, case_id, result)
            self._report(options, 1.0, "Investigation complete")

            case.status = CaseStatus.PAUSED if result.cancelled else CaseStatus.COMPLETE
            session.flush()
        finally:
            case_id_var.reset(token)

        log.info(
            "investigation.completed",
            **{k: v for k, v in result.as_dict().items() if isinstance(v, int | bool)},
        )
        return result

    # ------------------------------------------------------------- stages

    def _targets(
        self, session: Session, case_id: uuid.UUID, options: InvestigationOptions
    ) -> list[Target]:
        stmt = select(Target).where(Target.case_id == case_id)
        if options.target_ids:
            stmt = stmt.where(Target.id.in_(options.target_ids))
        return list(session.scalars(stmt.order_by(Target.created_at)))

    def plan(self, target: Target, options: InvestigationOptions) -> list:
        """Which collectors will run for ``target``."""
        return plan_collectors(
            target.type,
            include=options.include_collectors,
            exclude=options.exclude_collectors,
            settings=self.settings,
        )

    def _investigate_target(
        self,
        session: Session,
        case_id: uuid.UUID,
        target: Target,
        options: InvestigationOptions,
        result: InvestigationResult,
        job: Job | None,
    ) -> None:
        token = target_id_var.set(str(target.id))
        target.status = TargetStatus.RUNNING
        session.flush()
        try:
            collectors = self.plan(target, options)
            normalized = NormalizedTarget(
                type=target.type,
                raw_input=target.raw_input,
                value=target.normalized_value,
                attributes=dict(target.attributes or {}),
            )
            ctx = CollectorContext(case_id=case_id, target_id=target.id, settings=self.settings)
            outcomes = asyncio.run(
                run_all(
                    collectors,
                    normalized,
                    ctx,
                    concurrency=self.settings.collector_concurrency,
                    should_cancel=options.should_cancel,
                )
            )
            for outcome in outcomes:
                self._persist_outcome(session, case_id, target, outcome, result, job)
            target.status = (
                TargetStatus.COMPLETE
                if any(outcome.succeeded for outcome in outcomes)
                else TargetStatus.FAILED
            )
        except Exception as exc:
            target.status = TargetStatus.FAILED
            result.errors.append(
                {
                    "stage": "collection",
                    "target": target.normalized_value,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            log.exception("investigation.target_failed", target_id=str(target.id))
        finally:
            session.flush()
            target_id_var.reset(token)

    def _persist_outcome(
        self,
        session: Session,
        case_id: uuid.UUID,
        target: Target,
        outcome: RunOutcome,
        result: InvestigationResult,
        job: Job | None,
    ) -> None:
        """Write the run row, then its findings and their evidence."""
        run = CollectorRun(
            case_id=case_id,
            target_id=target.id,
            job_id=job.id if job is not None else None,
            collector=outcome.collector,
            collector_version=outcome.version,
            status=outcome.status,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=outcome.duration_ms,
            error_type=outcome.error_type,
            error_message=outcome.error_message,
            stats={
                **(outcome.result.stats if outcome.result else {}),
                "notes": outcome.notes[:20],
            },
        )
        session.add(run)
        session.flush()

        if outcome.status is RunStatus.FAILED or outcome.status is RunStatus.TIMEOUT:
            result.collectors_failed += 1
            result.errors.append(
                {
                    "stage": "collector",
                    "collector": outcome.collector,
                    "target": target.normalized_value,
                    "error_type": outcome.error_type or "unknown",
                    "error": outcome.error_message or "",
                }
            )
            return
        if outcome.status is RunStatus.SKIPPED:
            result.collectors_skipped += 1
            if outcome.error_message:
                result.notes.append(f"{outcome.collector}: {outcome.error_message}")
            return

        result.collectors_run += 1
        if outcome.result is None:
            return
        result.notes.extend(f"{outcome.collector}: {note}" for note in outcome.result.notes[:10])

        for draft in outcome.result.findings:
            finding = self._persist_finding(session, case_id, target, run, draft, result)
            if finding is None:
                continue
            payload = (
                outcome.result.payloads[draft.payload_index]
                if draft.payload_index is not None
                and draft.payload_index < len(outcome.result.payloads)
                else None
            )
            if payload is not None:
                self._persist_evidence(
                    session, case_id, finding, payload, outcome.collector, result
                )

    def _persist_finding(
        self,
        session: Session,
        case_id: uuid.UUID,
        target: Target,
        run: CollectorRun,
        draft: FindingDraft,
        result: InvestigationResult,
    ) -> Finding | None:
        """Filter, deduplicate and store one finding."""
        dedupe_key = draft.key(run.collector)
        existing = session.scalar(
            select(Finding).where(
                Finding.case_id == case_id,
                Finding.kind == draft.kind,
                Finding.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            result.findings_duplicate += 1
            return existing

        outcome = self.privacy.filter_finding(draft.data, declared=draft.classification)
        reasons = list(draft.confidence_reasons)
        reasons.extend(reason for reason in outcome.reasons if reason not in reasons)

        finding = Finding(
            case_id=case_id,
            target_id=target.id,
            run_id=run.id,
            kind=draft.kind,
            title=self.privacy.excerpt(draft.title, 500) or draft.title[:500],
            summary=draft.summary,
            data=outcome.data,
            collector=run.collector,
            source_url=draft.source_url,
            confidence=max(0.0, min(1.0, draft.confidence)),
            confidence_reasons=reasons[:20],
            classification=outcome.classification,
            redacted=outcome.redacted,
            dedupe_key=dedupe_key,
            observed_at=draft.observed_at,
        )
        session.add(finding)
        session.flush()
        result.findings_created += 1
        return finding

    def _persist_evidence(
        self,
        session: Session,
        case_id: uuid.UUID,
        finding: Finding,
        payload: RawPayload,
        collector: str,
        result: InvestigationResult,
    ) -> None:
        try:
            stored = self.evidence.store(
                session,
                case_id=case_id,
                collector=collector,
                source_url=payload.source_url,
                content=payload.content,
                content_type=payload.content_type,
                retrieved_at=payload.retrieved_at,
                finding=finding,
            )
        except Exception as exc:
            result.errors.append(
                {
                    "stage": "evidence",
                    "collector": collector,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            log.exception("investigation.evidence_failed", collector=collector)
            return
        if stored.created:
            result.evidence_stored += 1

    def _correlate(
        self, session: Session, case_id: uuid.UUID, result: InvestigationResult
    ) -> ResolutionSummary | None:
        """Extract entities from every finding in the case and resolve them."""
        try:
            findings = list(session.scalars(select(Finding).where(Finding.case_id == case_id)))
            summary = self.resolver.resolve(session, case_id, extract(findings))
        except Exception as exc:
            result.errors.append(
                {"stage": "correlation", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            )
            log.exception("investigation.correlation_failed", case_id=str(case_id))
            return None
        result.entities_created = summary.entities_created
        result.entities_updated = summary.entities_updated
        result.relationships_created = summary.relationships_created
        result.inferred_links = summary.inferred_links
        return summary

    def _build_timeline(
        self, session: Session, case_id: uuid.UUID, result: InvestigationResult
    ) -> None:
        try:
            events = build_timeline(session, case_id)
        except Exception as exc:
            result.errors.append(
                {"stage": "timeline", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            )
            log.exception("investigation.timeline_failed", case_id=str(case_id))
            return
        result.timeline_events = len(events)

    def _report(self, options: InvestigationOptions, fraction: float, message: str) -> None:
        if options.on_progress is None:
            return
        try:
            options.on_progress(max(0.0, min(1.0, fraction)), message)
        except Exception:
            log.warning("investigation.progress_hook_failed", message=message)


def run_investigation(
    session: Session,
    case_id: uuid.UUID,
    options: InvestigationOptions | None = None,
    *,
    job: Job | None = None,
    settings: Settings | None = None,
) -> InvestigationResult:
    """Convenience wrapper used by the CLI, the API and the Celery task."""
    return InvestigationEngine(settings).run(session, case_id, options, job=job)


def normalize_for(target: Target) -> NormalizedTarget:
    """Re-derive the normalised form of a stored target."""
    return normalize_target(target.raw_input, target.type)


def job_state_for(result: InvestigationResult) -> JobState:
    """Map a result onto the job state the API reports."""
    if result.cancelled:
        return JobState.CANCELLED
    return JobState.COMPLETE
