"""Running an investigation against a PERSON target.

Asserts the runtime consequence of the collector-applicability rules: the
engine schedules only what suits a name, and when the one applicable collector
is unconfigured it says so rather than leaving a silent gap.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.collectors.base import BaseCollector, CollectorResult, FindingDraft, RawPayload
from app.collectors.registry import _REGISTRY
from app.models import CollectorRun, Entity, Relationship, RunStatus
from app.models.enums import EntityType, FindingKind, RelationshipType, TargetType
from app.schemas.case import CaseCreate, TargetCreate
from app.services import cases as case_service
from app.services.engine import InvestigationEngine

NAME = "Timotheous Samar"


class StubInfrastructure(BaseCollector):
    """Stands in for DNS/RDAP/CT/HTTP. Must never run against a person."""

    name = "stub_infrastructure"
    supported_targets = [TargetType.DOMAIN, TargetType.URL, TargetType.IP]

    async def collect(self, target, ctx):  # pragma: no cover - must not be reached
        raise AssertionError(f"an infrastructure collector ran against {target.type}")


class StubPersonSearch(BaseCollector):
    """Stands in for the search collector: two hits sharing one name."""

    name = "stub_person_search"
    supported_targets = [TargetType.PERSON, TargetType.ORGANIZATION]

    urls = ("https://example.com/profile-a", "https://example.org/profile-b")

    async def collect(self, target, ctx):
        result = CollectorResult(stats={"results": len(self.urls)})
        for rank, url in enumerate(self.urls, start=1):
            payload = RawPayload(source_url=url, content={"url": url})
            result.add(
                FindingDraft(
                    kind=FindingKind.PERSON_CANDIDATE,
                    title=f"Page {rank}",
                    data={
                        "url": url,
                        "host": url.split("/")[2],
                        "title": f"Page {rank}",
                        "snippet": "",
                        "rank": rank,
                        "query": f'"{target.attributes["display_name"]}"',
                        "provider": "stub",
                        "subject_name": target.attributes["display_name"],
                        "subject_value": target.value,
                        "candidate_key": url,
                    },
                    confidence=0.2,
                    confidence_reasons=["Matched on displayed name only"],
                    dedupe_key=f"person-candidate:{target.value}:{url}",
                ),
                payload,
            )
        return result


class StubUnconfiguredSearch(BaseCollector):
    """A person-capable collector that has no credential configured."""

    name = "stub_unconfigured_search"
    supported_targets = [TargetType.PERSON]
    requires_api_key = True

    def is_available(self):
        return False, (
            "No search provider is configured. Set SEARCH_PROVIDER to brave, bing or "
            "serper and supply the matching API key."
        )

    async def collect(self, target, ctx):  # pragma: no cover - never reached
        raise AssertionError("an unavailable collector was executed")


@pytest.fixture
def person_registry(monkeypatch, tmp_path):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    reset_settings_cache()
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)

    original = dict(_REGISTRY)
    _REGISTRY.clear()
    for cls in (StubInfrastructure, StubPersonSearch):
        _REGISTRY[cls.name] = cls
    yield _REGISTRY
    _REGISTRY.clear()
    _REGISTRY.update(original)
    reset_settings_cache()


@pytest.fixture
def person_case(db_session):
    created = case_service.create_case(db_session, CaseCreate(name="Person test"))
    case_service.add_target(
        db_session, created.id, TargetCreate(value=NAME, type=TargetType.PERSON)
    )
    db_session.commit()
    return created


def test_only_person_applicable_collectors_are_scheduled(db_session, person_case, person_registry):
    result = InvestigationEngine().run(db_session, person_case.id)

    runs = db_session.scalars(
        select(CollectorRun).where(CollectorRun.case_id == person_case.id)
    ).all()
    assert {run.collector for run in runs} == {"stub_person_search"}
    assert result.collectors_failed == 0


def test_same_name_hits_become_separate_candidates(db_session, person_case, person_registry):
    InvestigationEngine().run(db_session, person_case.id)

    personas = db_session.scalars(
        select(Entity).where(Entity.case_id == person_case.id, Entity.type == EntityType.PERSONA)
    ).all()
    candidates = [e for e in personas if e.canonical_value.startswith("person-candidate:")]
    subjects = [e for e in personas if e.canonical_value.startswith("person:")]

    assert len(candidates) == 2, "two pages naming one person collapsed into a single entity"
    assert len(subjects) == 1
    assert all(entity.attributes["identity_established"] is False for entity in candidates)


def test_candidate_links_stay_below_the_merge_threshold(db_session, person_case, person_registry):
    InvestigationEngine().run(db_session, person_case.id)

    links = db_session.scalars(
        select(Relationship).where(
            Relationship.case_id == person_case.id,
            Relationship.type == RelationshipType.POSSIBLY_SAME_ENTITY,
        )
    ).all()
    assert links, "the subject was not linked to its candidates at all"
    for link in links:
        assert link.confidence <= 0.30
        assert link.attributes["basis"] == "name_match_only"
        assert any(
            "not unique" in reason or "does not identify" in reason
            for reason in link.confidence_reasons
        )


def test_an_unconfigured_collector_is_recorded_as_skipped_with_its_reason(
    db_session, person_case, person_registry, monkeypatch
):
    """A person case with nothing configured must explain itself, not look empty."""
    _REGISTRY.clear()
    _REGISTRY[StubUnconfiguredSearch.name] = StubUnconfiguredSearch

    result = InvestigationEngine().run(db_session, person_case.id)

    runs = db_session.scalars(
        select(CollectorRun).where(CollectorRun.case_id == person_case.id)
    ).all()
    assert len(runs) == 1
    assert runs[0].status is RunStatus.SKIPPED
    assert "SEARCH_PROVIDER" in (runs[0].error_message or "")
    assert result.collectors_skipped == 1
    assert result.collectors_failed == 0
