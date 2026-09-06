"""A PERSON investigation must be useful at zero cost.

The property under test is the whole point of the free-source path: with
``SEARCH_PROVIDER=none`` and no API key anywhere, a PERSON case still produces
candidates, still keeps same-name people apart, and still explains itself.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.collectors.registry import load_builtin_collectors, plan_collectors
from app.models import CollectorRun, Entity, Relationship, RunStatus
from app.models.enums import EntityType, RelationshipType, TargetType
from app.schemas.case import CaseCreate, PersonContext, TargetCreate
from app.services import cases as case_service
from app.services.engine import InvestigationEngine

NAME = "Timotheous Samar"

#: Two ORCID records with the same name and different institutions: the shape of
#: the problem this whole path exists to handle.
ORCID_BODY = {
    "expanded-result": [
        {
            "orcid-id": "0000-0002-1825-0097",
            "given-names": "Timotheous",
            "family-names": "Samar",
            "institution-name": ["Example University"],
        },
        {
            "orcid-id": "0000-0001-5109-3700",
            "given-names": "Timotheous",
            "family-names": "Samar",
            "institution-name": ["Unrelated Institute"],
        },
    ]
}


@pytest.fixture
def free_sources(monkeypatch, tmp_path):
    """Every free endpoint mocked; no key configured anywhere."""
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    for key in ("BRAVE_API_KEY", "BING_API_KEY", "SERPER_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(key, "")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _mock_all_free_endpoints() -> None:
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(200, json=ORCID_BODY)
    )
    respx.get("https://api.openalex.org/authors").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={"search": []})
    )
    respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    # Reddit refusing anonymous access is the common real-world case.
    respx.get("https://www.reddit.com/search.json").mock(return_value=httpx.Response(403))


def _case(db_session, context: PersonContext | None = None):
    created = case_service.create_case(db_session, CaseCreate(name="Zero-cost person"))
    case_service.add_target(
        db_session,
        created.id,
        TargetCreate(value=NAME, type=TargetType.PERSON, context=context),
    )
    db_session.commit()
    return created


def test_every_free_person_collector_needs_no_api_key():
    load_builtin_collectors()
    planned = plan_collectors(TargetType.PERSON)
    free = [collector for collector in planned if not collector.requires_api_key]

    assert {collector.name for collector in free} == {
        "crossref",
        "github_people",
        "openalex",
        "orcid",
        "person_usernames",
        "reddit",
        "wikidata",
    }
    for collector in free:
        available, reason = collector.is_available()
        assert available is True, f"{collector.name} is not available: {reason}"
        assert collector.configuration().required_settings == []


def test_only_the_search_collector_needs_payment():
    load_builtin_collectors()
    paid = [c.name for c in plan_collectors(TargetType.PERSON) if c.requires_api_key]
    assert paid == ["search"]


@respx.mock
def test_a_person_case_produces_candidates_with_no_provider_configured(
    db_session, free_sources, mock_http
):
    _mock_all_free_endpoints()
    case = _case(db_session)

    result = InvestigationEngine().run(db_session, case.id)

    assert result.findings_created > 0, "the zero-cost path produced nothing"
    candidates = db_session.scalars(
        select(Entity).where(Entity.case_id == case.id, Entity.type == EntityType.PERSONA)
    ).all()
    assert len([e for e in candidates if e.canonical_value.startswith("person-candidate:")]) == 2


@respx.mock
def test_the_paid_search_collector_is_skipped_not_failed(db_session, free_sources, mock_http):
    _mock_all_free_endpoints()
    case = _case(db_session)

    InvestigationEngine().run(db_session, case.id)

    runs = {
        run.collector: run
        for run in db_session.scalars(select(CollectorRun).where(CollectorRun.case_id == case.id))
    }
    assert runs["search"].status is RunStatus.SKIPPED
    assert "SEARCH_PROVIDER" in (runs["search"].error_message or "")
    # A skipped paid collector must not make the investigation look failed.
    assert runs["orcid"].status is RunStatus.SUCCESS


@respx.mock
def test_reddit_refusing_anonymous_access_is_recorded_as_skipped(
    db_session, free_sources, mock_http
):
    _mock_all_free_endpoints()
    case = _case(db_session)

    result = InvestigationEngine().run(db_session, case.id)

    runs = {
        run.collector: run
        for run in db_session.scalars(select(CollectorRun).where(CollectorRun.case_id == case.id))
    }
    assert runs["reddit"].status is RunStatus.SKIPPED
    assert "Reddit" in (runs["reddit"].error_message or "")
    assert result.collectors_failed == 0


@respx.mock
def test_same_name_candidates_stay_apart_at_zero_cost(db_session, free_sources, mock_http):
    _mock_all_free_endpoints()
    case = _case(db_session)

    InvestigationEngine().run(db_session, case.id)

    links = db_session.scalars(
        select(Relationship).where(
            Relationship.case_id == case.id,
            Relationship.type == RelationshipType.POSSIBLY_SAME_ENTITY,
        )
    ).all()
    assert len(links) == 2
    for link in links:
        assert link.confidence <= 0.30
        assert link.attributes["basis"] == "name_match_only"
        assert link.attributes["requires_corroboration"] is True


@respx.mock
def test_supplied_context_raises_only_the_corroborated_candidate(
    db_session, free_sources, mock_http
):
    """The discriminating case: one of two same-name records matches the
    supplied affiliation, and only that one should rise."""
    _mock_all_free_endpoints()
    case = _case(db_session, PersonContext(organizations=["Example University"]))

    InvestigationEngine().run(db_session, case.id)

    candidates = {
        entity.attributes.get("source_record", entity.canonical_value): entity
        for entity in db_session.scalars(
            select(Entity).where(Entity.case_id == case.id, Entity.type == EntityType.PERSONA)
        )
        if entity.canonical_value.startswith("person-candidate:")
    }
    matched = next(e for k, e in candidates.items() if "1825" in k)
    unmatched = next(e for k, e in candidates.items() if "5109" in k)

    assert matched.attributes["corroborated_by"] == ["affiliation"]
    assert matched.confidence > unmatched.confidence
    assert unmatched.attributes["corroborated_by"] == []
    # Corroborated or not, neither is asserted to be the person.
    assert matched.attributes["identity_established"] is False
    assert matched.confidence < 0.95


@respx.mock
def test_every_candidate_carries_reasons_for_and_against(db_session, free_sources, mock_http):
    _mock_all_free_endpoints()
    case = _case(db_session)

    InvestigationEngine().run(db_session, case.id)

    candidates = [
        entity
        for entity in db_session.scalars(
            select(Entity).where(Entity.case_id == case.id, Entity.type == EntityType.PERSONA)
        )
        if entity.canonical_value.startswith("person-candidate:")
    ]
    assert candidates
    for candidate in candidates:
        assert candidate.attributes["match_reasons"], "no reason the candidate may match"
        assert candidate.attributes["mismatch_reasons"], "no reason it may not"
        assert candidate.attributes["source"] == "orcid"
        assert candidate.attributes["source_label"] == "ORCID"
