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
from app.core import http
from app.core.errors import NotFoundError
from app.core.logging import case_id_var, get_logger, target_id_var
from app.core.settings import Settings, get_settings
from app.correlation.corroboration import CorroborationService
from app.correlation.extraction import extract
from app.correlation.resolver import EntityResolver, ResolutionSummary
from app.models import (
    Case,
    CaseStatus,
    CollectorRun,
    Entity,
    Finding,
    Job,
    Relationship,
    RunStatus,
    Target,
    TargetStatus,
    TargetType,
)
from app.models.enums import JobState, ObservationStage, ObservationSubject
from app.privacy.filter import PrivacyFilter
from app.services.evidence import EvidenceStore
from app.services.normalization import NormalizedTarget, normalize_target
from app.services.observations import LEDGER_FLAG, ExecutionLedger, write_ledger
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
    #: Identifiers two independently operated sources both published.
    corroborations: int = 0
    #: Public evidence promoted out of collector payloads into structures the
    #: investigator can actually see and review.
    search_results: int = 0
    social_profiles: int = 0
    images: int = 0
    public_contacts: int = 0
    cancelled: bool = False
    #: How many observation rows this execution wrote. Zero means the execution
    #: observed nothing — distinguishable from a legacy execution, which has no
    #: ledger flag on its job at all.
    observations: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Objects this execution touched, accumulated during the run and written once
    #: at the end. Not serialised: it is working state, not a counter.
    ledger: ExecutionLedger = field(default_factory=ExecutionLedger)

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
            "corroborations": self.corroborations,
            "social_profiles": self.social_profiles,
            "images": self.images,
            "public_contacts": self.public_contacts,
            "cancelled": self.cancelled,
            "observations": self.observations,
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
        corroboration: CorroborationService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.privacy = privacy or PrivacyFilter(self.settings)
        self.evidence = evidence or EvidenceStore(self.settings, self.privacy)
        self.resolver = resolver or EntityResolver()
        self.corroboration = corroboration or CorroborationService()
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
            self._record_anchors(session, targets, job)
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

            # Before correlation, so provider results are resolved into
            # candidates and promoted by the stages that follow rather than
            # sitting outside the pipeline.
            self._search(session, case_id, result, job)
            self._report(options, 0.9, "Searching public web sources")
            self._correlate(session, case_id, result)
            self._report(options, 0.95, "Correlating entities")
            self._build_timeline(session, case_id, result)
            self._record_observations(session, case_id, result, job)
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
            outcomes = asyncio.run(self._collect(collectors, normalized, ctx, options))
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
            source_attribution=outcome.attribution or None,
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
            # Observed again by *this* execution, even though the canonical row
            # was created by an earlier one. Recording that is the whole point:
            # overwriting `run_id` would throw away which execution first saw it,
            # and leaving it alone would make this execution look blind.
            result.ledger.touch(
                ObservationSubject.FINDING,
                existing.id,
                stage=ObservationStage.COLLECTION,
                collector=run.collector,
                run_id=run.id,
                source_url=existing.source_url,
            )
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
        result.ledger.touch(
            ObservationSubject.FINDING,
            finding.id,
            stage=ObservationStage.COLLECTION,
            collector=run.collector,
            run_id=run.id,
            source_url=finding.source_url,
            first_seen=True,
        )
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
        result.ledger.touch(
            ObservationSubject.EVIDENCE,
            stored.evidence.id,
            stage=ObservationStage.COLLECTION,
            collector=collector,
            source_url=payload.source_url,
            first_seen=stored.created,
        )

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

        # Cross-source corroboration runs after resolution, so it sees every
        # candidate every collector produced and can spot the ones two
        # independent sources agree about.
        try:
            corroboration = self.corroboration.run(session, case_id)
        except Exception as exc:  # pragma: no cover - defensive, like the stages above
            result.errors.append(
                {
                    "stage": "corroboration",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            log.exception("investigation.corroboration_failed", case_id=str(case_id))
        else:
            result.corroborations = len(corroboration.corroborations)
            result.notes.extend(item.reason for item in corroboration.corroborations)

        self._promote(session, case_id, result)
        self._observe_graph(session, case_id, result)
        return summary

    def _observe_graph(
        self, session: Session, case_id: uuid.UUID, result: InvestigationResult
    ) -> None:
        """Snapshot the entities and relationships this execution resolved.

        After corroboration, deliberately: the corroboration pass rescores
        entities, and an execution report must show the score the execution
        finished with rather than the one it held halfway through.

        Scoped to the case rather than to a diff, because resolution is a
        whole-case operation: every entity and edge in the case is re-derived from
        every finding on every run, so this execution genuinely did observe all of
        them. That is a different claim from "this execution found them", which is
        what ``first_seen`` carries.
        """
        try:
            for entity in session.scalars(select(Entity).where(Entity.case_id == case_id)):
                result.ledger.touch(
                    ObservationSubject.ENTITY,
                    entity.id,
                    stage=ObservationStage.CORRELATION,
                    collector="correlation",
                )
            for edge in session.scalars(
                select(Relationship).where(Relationship.case_id == case_id)
            ):
                result.ledger.touch(
                    ObservationSubject.RELATIONSHIP,
                    edge.id,
                    stage=ObservationStage.CORRELATION,
                    collector=edge.collector,
                )
        except Exception as exc:  # pragma: no cover - defensive, like the stages above
            result.errors.append(
                {"stage": "observations", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            )
            log.exception("investigation.graph_observation_failed", case_id=str(case_id))

    def _search(
        self,
        session: Session,
        case_id: uuid.UUID,
        result: InvestigationResult,
        job: Job | None,
    ) -> None:
        """Search the public web, when a provider is configured.

        A no-op that *records itself* otherwise. The coverage table has to be
        able to say "the public web was not searched because no provider is
        configured", and it can only say that if the attempt leaves a trace.
        """
        from app.services.search_ingest import SEARCH_COLLECTOR

        ingest: dict[str, Any] | None = None
        try:
            for target in session.scalars(
                select(Target).where(Target.case_id == case_id, Target.type == TargetType.PERSON)
            ):
                # A run row for the search stage, so automatically ingested
                # results cannot exist outside execution accounting. Without it
                # the stage was recorded only on the job's JSON result: invisible
                # to the runs table, and its findings carried no run at all.
                run = self._search_run(session, case_id=case_id, target=target, job=job)
                report = asyncio.run(
                    self._search_one(session, case_id=case_id, target_id=target.id)
                )
                ingest = report.to_dict()
                result.search_results += report.results_stored
                self._close_search_run(session, run, report)
                for finding_id in report.findings:
                    finding = session.get(Finding, finding_id)
                    if finding is None:
                        continue
                    if finding.run_id is None:
                        # Only when it has none: a finding first produced by a
                        # collector keeps the run that produced it.
                        finding.run_id = run.id
                    new = finding.id in set(report.new_findings)
                    result.ledger.touch(
                        ObservationSubject.FINDING,
                        finding.id,
                        stage=ObservationStage.SEARCH,
                        collector=SEARCH_COLLECTOR,
                        run_id=run.id,
                        source_url=finding.source_url,
                        first_seen=new,
                    )
                    # The artefacts the ingest stored for it. Without these an
                    # execution report cites no evidence, which for a forensic
                    # report is the one thing it must always be able to do.
                    for artefact in finding.evidence or []:
                        result.ledger.touch(
                            ObservationSubject.EVIDENCE,
                            artefact.id,
                            stage=ObservationStage.SEARCH,
                            collector=SEARCH_COLLECTOR,
                            run_id=run.id,
                            source_url=artefact.source_url,
                            first_seen=new,
                        )
                session.flush()
                if not report.configured:
                    # One unconfigured provider is the same answer for every
                    # target; saying it once is enough.
                    break
        except Exception as exc:  # pragma: no cover - defensive, like the stages above
            result.errors.append(
                {"stage": "search", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            )
            log.exception("investigation.search_failed", case_id=str(case_id))
        if ingest is not None and job is not None:
            # Recorded on the job so the report describes what happened rather
            # than re-deriving it from whatever the settings say at render time.
            job.result = {**dict(job.result or {}), "search_ingest": ingest}
            session.flush()

    def _search_run(
        self, session: Session, *, case_id: uuid.UUID, target: Target, job: Job | None
    ) -> CollectorRun:
        """The run row for this execution's search stage."""
        from app.services.search_ingest import SEARCH_COLLECTOR, SEARCH_SOURCE_LABEL

        run = CollectorRun(
            case_id=case_id,
            target_id=target.id,
            job_id=job.id if job is not None else None,
            collector=SEARCH_COLLECTOR,
            collector_version="1.0.0",
            source_attribution=SEARCH_SOURCE_LABEL,
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        return run

    def _close_search_run(self, session: Session, run: CollectorRun, report: Any) -> None:
        """Finish the search run with the status the ingest actually earned.

        An unconfigured provider is SKIPPED, not FAILED: nothing broke, a channel
        was switched off. Queries that ran and returned nothing are a SUCCESS with
        no findings, which the coverage table reads as a real absence for this
        source only.
        """
        run.finished_at = datetime.now(UTC)
        run.stats = {
            "queries_run": report.queries_run,
            "image_queries_run": report.image_queries_run,
            "results_seen": report.results_seen,
            "results_stored": report.results_stored,
            "duplicates": report.duplicates,
            "rejected_urls": report.rejected_urls,
            "findings": len(report.findings),
        }
        if not report.configured:
            run.status = RunStatus.SKIPPED
            run.error_message = report.reason or "No search provider is configured"
        elif report.failures and not report.queries_run:
            run.status = RunStatus.FAILED
            first = report.failures[0]
            run.error_type = str(first.get("error_type") or "ProviderError")
            run.error_message = str(first.get("error") or "")[:2000]
        elif report.failures:
            run.status = RunStatus.PARTIAL
            run.error_message = f"{len(report.failures)} query(ies) failed"
        else:
            run.status = RunStatus.SUCCESS
        session.flush()

    async def _search_one(self, session: Session, *, case_id: uuid.UUID, target_id: uuid.UUID):
        from app.services.search_ingest import search_target

        try:
            return await search_target(session, case_id=case_id, target_id=target_id)
        finally:
            await http.close_owned_client()

    def _promote(self, session: Session, case_id: uuid.UUID, result: InvestigationResult) -> None:
        """Surface the public data collectors already retrieved.

        Runs after resolution, so the candidate entities exist to attach to: a
        profile or an avatar recorded before its candidate would be orphaned in
        the UI, which is the state this whole stage exists to end.

        Wrapped like the stages above — promotion is presentation of data
        already collected and stored, so a failure here must not lose the run.
        """
        from app.services.promotion import (
            PromotionTrace,
            candidate_for_finding,
            promote_finding,
        )

        trace = PromotionTrace()
        try:
            from app.services.promotion import PROMOTABLE_KINDS

            findings = list(
                session.scalars(
                    select(Finding).where(
                        Finding.case_id == case_id,
                        Finding.kind.in_(tuple(PROMOTABLE_KINDS)),
                    )
                )
            )
            targets = {
                target.id: target
                for target in session.scalars(select(Target).where(Target.case_id == case_id))
            }
            for finding in findings:
                url = str((finding.data or {}).get("url") or "")
                candidate = candidate_for_finding(session, case_id, url) if url else None
                counts = promote_finding(
                    session,
                    case_id=case_id,
                    finding=finding,
                    target=targets.get(finding.target_id) if finding.target_id else None,
                    candidate_entity_id=candidate.id if candidate else None,
                    touched=trace,
                )
                result.social_profiles += counts["profiles"]
                result.images += counts["images"]
                result.public_contacts += counts["contacts"]
            session.flush()
            # Every derived object this execution created or refreshed, so an
            # execution report can show the profiles, contacts and images *this*
            # run surfaced rather than everything the case has ever held.
            for subject_type, ids in (
                (ObservationSubject.SOCIAL_PROFILE, trace.profiles),
                (ObservationSubject.PUBLIC_CONTACT, trace.contacts),
                (ObservationSubject.IMAGE_EVIDENCE, trace.images),
            ):
                for subject_id in ids:
                    result.ledger.touch(
                        subject_type,
                        subject_id,
                        stage=ObservationStage.PROMOTION,
                        collector="promotion",
                    )
        except Exception as exc:  # pragma: no cover - defensive, like the stages above
            result.errors.append(
                {
                    "stage": "promotion",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            log.exception("investigation.promotion_failed", case_id=str(case_id))

    async def _collect(
        self,
        collectors: list[Any],
        normalized: NormalizedTarget,
        ctx: CollectorContext,
        options: Any,
    ) -> list[Any]:
        """Run the collectors, then tear the HTTP pool down inside this loop.

        Each target gets its own ``asyncio.run``, and a connection pool outlives
        neither its loop nor this function. Closing here — rather than leaving
        the module-level client for the next loop to inherit — is what keeps a
        later run from reusing a keep-alive connection whose loop has been
        closed, which surfaced as an intermittent "Event loop is closed".
        """
        try:
            return list(
                await run_all(
                    collectors,
                    normalized,
                    ctx,
                    concurrency=self.settings.collector_concurrency,
                    should_cancel=options.should_cancel,
                )
            )
        finally:
            await http.close_owned_client()

    def _record_anchors(self, session: Session, targets: list[Target], job: Job | None) -> None:
        """Snapshot the anchors in force as this execution starts.

        An execution report has to be able to say what the investigator had
        supplied *at the time*, because that is what its correlation rests on. The
        anchors live on the target, which is mutable — supplying a username before
        a rerun is the whole point — so reading them at render time would show a
        later run's anchors beside an earlier run's scores.
        """
        if job is None:
            return
        anchors = {
            str(target.id): {
                "display_name": (target.attributes or {}).get("display_name"),
                "normalized_value": target.normalized_value,
                "type": str(target.type),
                "context": dict((target.attributes or {}).get("context") or {}),
            }
            for target in targets
        }
        job.result = {**dict(job.result or {}), "anchors": anchors}
        session.flush()

    def _record_observations(
        self,
        session: Session,
        case_id: uuid.UUID,
        result: InvestigationResult,
        job: Job | None,
    ) -> None:
        """Write this execution's observation ledger, once, at the end.

        Last, so every snapshot is of the state the execution actually left — a
        profile promoted in one stage and rescored by corroboration in another has
        one truthful snapshot rather than two that disagree.

        Wrapped like every other stage: a failure here loses the ledger for this
        execution, which the report can say honestly, and must not lose the run.
        """
        try:
            written = write_ledger(
                session,
                case_id=case_id,
                job_id=job.id if job is not None else None,
                ledger=result.ledger,
            )
        except Exception as exc:  # pragma: no cover - defensive, like the stages above
            result.errors.append(
                {"stage": "observations", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            )
            log.exception("investigation.observations_failed", case_id=str(case_id))
            return
        result.observations = written
        if job is not None:
            # The flag, not the row count, is what tells a legacy execution (no
            # ledger was ever written) from one that genuinely observed nothing.
            job.result = {**dict(job.result or {}), LEDGER_FLAG: True}
            session.flush()

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
