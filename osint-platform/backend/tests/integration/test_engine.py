"""The investigation engine end to end, against stub collectors."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.collectors.base import (
    BaseCollector,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import _REGISTRY
from app.models import (
    Case,
    CaseStatus,
    CollectorRun,
    Entity,
    Evidence,
    Finding,
    Relationship,
    RunStatus,
    Target,
    TargetStatus,
    TimelineEvent,
)
from app.models.enums import Classification, FindingKind, TargetType
from app.schemas.case import CaseCreate, TargetCreate
from app.services import cases as case_service
from app.services.engine import InvestigationEngine, InvestigationOptions


class StubDNS(BaseCollector):
    """Stands in for the DNS collector: succeeds and yields evidence."""

    name = "stub_dns"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult(stats={"queries": 1})
        payload = RawPayload(
            source_url=f"dns://{target.value}/A",
            content={"hostname": target.value, "records": ["93.184.215.14"]},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.DNS_RECORD,
                title=f"DNS A for {target.value}",
                data={
                    "hostname": target.value,
                    "record_type": "A",
                    "records": ["93.184.215.14"],
                },
                confidence=0.95,
                confidence_reasons=["Resolved from public DNS"],
                dedupe_key=f"dns:{target.value}:A",
            ),
            payload,
        )
        return result


class StubRegistration(BaseCollector):
    """Yields a dated finding so the timeline has something to build from."""

    name = "stub_rdap"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        from datetime import UTC, datetime

        result = CollectorResult()
        payload = RawPayload(
            source_url=f"https://rdap.org/domain/{target.value}",
            content={"ldhName": target.value},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.DOMAIN_REGISTRATION,
                title=f"Registration for {target.value}",
                data={
                    "handle": target.value,
                    "registrar": "Example Registrar LLC",
                    "registrant_organization": "Example Documentation Trust",
                    "privacy_protected": False,
                },
                observed_at=datetime(1995, 8, 14, tzinfo=UTC),
                confidence=0.9,
                dedupe_key=f"rdap:{target.value}",
            ),
            payload,
        )
        return result


class StubFailing(BaseCollector):
    name = "stub_failing"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        from app.core.errors import CollectorError

        raise CollectorError("the upstream service refused the query")


class StubNeedsKey(BaseCollector):
    name = "stub_needs_key"
    supported_targets = [TargetType.DOMAIN]
    requires_api_key = True

    def is_available(self):
        return False, "STUB_API_KEY is not configured"

    async def collect(self, target, ctx):  # pragma: no cover - never reached
        return CollectorResult()


class StubLeaky(BaseCollector):
    """Returns something credential-shaped, to prove the filter runs."""

    name = "stub_leaky"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult()
        payload = RawPayload(
            source_url="https://example.com/config",
            content={"config": "AKIA1234567890ABCDEF"},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.HTTP_METADATA,
                title="Configuration file",
                data={"api_key": "AKIA1234567890ABCDEF", "url": "https://example.com/config"},
                classification=Classification.PUBLIC,
                dedupe_key="leaky:1",
            ),
            payload,
        )
        return result


@pytest.fixture
def stub_registry(monkeypatch, tmp_path):
    """Replace the registry with stubs so no test touches the network."""
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    reset_settings_cache()

    # The engine loads the built-in collectors on construction; suppress that
    # so the stub registry is the only thing that can run. No test in this
    # suite is permitted to reach the network.
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)

    original = dict(_REGISTRY)
    _REGISTRY.clear()
    for cls in (StubDNS, StubRegistration, StubFailing, StubNeedsKey):
        _REGISTRY[cls.name] = cls
    yield _REGISTRY
    _REGISTRY.clear()
    _REGISTRY.update(original)
    reset_settings_cache()


@pytest.fixture
def case(db_session):
    created = case_service.create_case(db_session, CaseCreate(name="Engine test"))
    case_service.add_target(db_session, created.id, TargetCreate(value="example.com"))
    db_session.commit()
    return created


def test_full_pipeline_produces_the_whole_case(db_session, case, stub_registry):
    result = InvestigationEngine().run(db_session, case.id)

    assert result.targets == 1
    assert result.collectors_run == 2
    assert result.collectors_failed == 1
    assert result.collectors_skipped == 1
    assert result.findings_created == 2
    assert result.evidence_stored == 2
    assert result.entities_created >= 3
    assert result.relationships_created >= 2
    assert result.timeline_events == 1

    assert db_session.scalar(select(Case).where(Case.id == case.id)).status is CaseStatus.COMPLETE
    target = db_session.scalar(select(Target).where(Target.case_id == case.id))
    assert target.status is TargetStatus.COMPLETE


def test_a_failing_collector_does_not_abort_the_run(db_session, case, stub_registry):
    result = InvestigationEngine().run(db_session, case.id)

    runs = {run.collector: run for run in db_session.scalars(select(CollectorRun))}
    assert runs["stub_failing"].status is RunStatus.FAILED
    assert "refused the query" in runs["stub_failing"].error_message
    assert runs["stub_dns"].status is RunStatus.SUCCESS
    assert any(error["collector"] == "stub_failing" for error in result.errors)


def test_missing_credentials_are_skipped_with_a_reason(db_session, case, stub_registry):
    result = InvestigationEngine().run(db_session, case.id)
    run = db_session.scalar(select(CollectorRun).where(CollectorRun.collector == "stub_needs_key"))
    assert run.status is RunStatus.SKIPPED
    assert "STUB_API_KEY" in run.error_message
    assert any("STUB_API_KEY" in note for note in result.notes)


def test_evidence_is_linked_to_its_finding(db_session, case, stub_registry):
    InvestigationEngine().run(db_session, case.id)
    for evidence in db_session.scalars(select(Evidence)):
        assert evidence.findings, "every stored artefact must cite the finding it supports"
        assert len(evidence.sha256) == 64
        assert evidence.source_url


def test_findings_carry_confidence_reasons(db_session, case, stub_registry):
    InvestigationEngine().run(db_session, case.id)
    findings = list(db_session.scalars(select(Finding)))
    assert findings
    assert all(
        finding.confidence_reasons for finding in findings if finding.collector == "stub_dns"
    )


def test_privacy_filter_runs_before_persistence(db_session, case, stub_registry):
    stub_registry["stub_leaky"] = StubLeaky
    InvestigationEngine().run(db_session, case.id)

    finding = db_session.scalar(select(Finding).where(Finding.collector == "stub_leaky"))
    assert finding is not None
    assert "AKIA1234567890ABCDEF" not in str(finding.data)
    assert finding.classification is Classification.RESTRICTED
    assert finding.redacted is True

    evidence = db_session.scalar(select(Evidence).where(Evidence.collector == "stub_leaky"))
    assert "AKIA1234567890ABCDEF" not in (evidence.excerpt or "")


def test_rerunning_deduplicates_findings(db_session, case, stub_registry):
    first = InvestigationEngine().run(db_session, case.id)
    second = InvestigationEngine().run(db_session, case.id)

    assert second.findings_created == 0
    assert second.findings_duplicate == first.findings_created
    assert db_session.scalar(select(Case).where(Case.id == case.id)).status is CaseStatus.COMPLETE


def test_entities_and_relationships_are_correlated(db_session, case, stub_registry):
    InvestigationEngine().run(db_session, case.id)
    entities = {e.canonical_value for e in db_session.scalars(select(Entity))}
    assert "example.com" in entities
    assert "93.184.215.14" in entities
    relationships = list(db_session.scalars(select(Relationship)))
    assert relationships
    assert all(edge.confidence_reasons for edge in relationships)


def test_timeline_is_rebuilt_not_duplicated(db_session, case, stub_registry):
    InvestigationEngine().run(db_session, case.id)
    InvestigationEngine().run(db_session, case.id)
    events = list(db_session.scalars(select(TimelineEvent)))
    assert len(events) == 1
    assert events[0].occurred_at.year == 1995


def test_collector_selection_is_honoured(db_session, case, stub_registry):
    result = InvestigationEngine().run(
        db_session, case.id, InvestigationOptions(include_collectors=["stub_dns"])
    )
    assert result.collectors_run == 1
    assert {run.collector for run in db_session.scalars(select(CollectorRun))} == {"stub_dns"}


def test_collector_exclusion_is_honoured(db_session, case, stub_registry):
    InvestigationEngine().run(
        db_session, case.id, InvestigationOptions(exclude_collectors=["stub_failing"])
    )
    assert "stub_failing" not in {run.collector for run in db_session.scalars(select(CollectorRun))}


def test_cancellation_stops_before_the_next_target(db_session, case, stub_registry):
    case_service.add_target(db_session, case.id, TargetCreate(value="example.org"))
    db_session.commit()

    result = InvestigationEngine().run(
        db_session, case.id, InvestigationOptions(should_cancel=lambda: True)
    )
    assert result.cancelled is True
    assert result.collectors_run == 0
    assert db_session.scalar(select(Case).where(Case.id == case.id)).status is CaseStatus.PAUSED


def test_progress_is_reported(db_session, case, stub_registry):
    seen: list[tuple[float, str]] = []
    InvestigationEngine().run(
        db_session, case.id, InvestigationOptions(on_progress=lambda f, m: seen.append((f, m)))
    )
    assert seen
    assert seen[-1][0] == 1.0


def test_a_failing_progress_hook_does_not_fail_the_run(db_session, case, stub_registry):
    def explode(fraction, message):
        raise RuntimeError("hook is broken")

    result = InvestigationEngine().run(
        db_session, case.id, InvestigationOptions(on_progress=explode)
    )
    assert result.findings_created == 2


def test_case_without_targets_completes_with_a_note(db_session, stub_registry):
    empty = case_service.create_case(db_session, CaseCreate(name="Empty"))
    db_session.commit()
    result = InvestigationEngine().run(db_session, empty.id)
    assert result.targets == 0
    assert result.notes
    assert db_session.scalar(select(Case).where(Case.id == empty.id)).status is CaseStatus.COMPLETE


def test_unknown_case_raises(db_session, stub_registry):
    import uuid

    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        InvestigationEngine().run(db_session, uuid.uuid4())
