"""Acceptance: a whole PERSON case, at zero cost, with SEARCH_PROVIDER=none.

One case, one subject, every stage: free structured collectors run, recon
queries are generated, the investigator imports public results by hand, social
and publication URLs are classified, an image is stored as context evidence,
anchors correlate conservatively, explanations name the real source, and a
report comes out the other end.

Every endpoint is mocked, so the test is deterministic and touches no network.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.collectors.person import PersonContext
from app.correlation.confidence import AUTO_MERGE_THRESHOLD
from app.models import CollectorRun, Entity, Finding, Relationship, RunStatus
from app.models.enums import EntityType, FindingKind, RelationshipType, TargetType
from app.reporting.model import build_report
from app.schemas.case import CaseCreate, TargetCreate
from app.schemas.case import PersonContext as PersonContextSchema
from app.schemas.recon import ManualResult, ManualResultImport
from app.services import cases as case_service
from app.services import recon_import
from app.services.engine import InvestigationEngine
from app.services.recon import generate_queries

NAME = "Timotheous Samar"
ORCID = "0000-0002-1825-0097"

#: Two same-name researchers, one of whom carries the supplied ORCID.
ORCID_BODY = {
    "expanded-result": [
        {
            "orcid-id": ORCID,
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

#: OpenAlex publishes the same ORCID — an independent source agreeing.
OPENALEX_BODY = {
    "results": [
        {
            "id": "https://openalex.org/A5023888391",
            "display_name": NAME,
            "orcid": f"https://orcid.org/{ORCID}",
            "works_count": 12,
            "last_known_institutions": [
                {"display_name": "Example University", "country_code": "NL"}
            ],
        }
    ]
}

GITHUB_SEARCH = {
    "total_count": 1,
    "items": [{"login": "tsamar", "html_url": "https://github.com/tsamar"}],
}
GITHUB_PROFILE = {"name": NAME, "company": "Example University", "location": "Delft"}


@pytest.fixture
def zero_cost(monkeypatch, tmp_path):
    """No search provider, no credentials anywhere."""
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    for key in ("BRAVE_API_KEY", "BING_API_KEY", "SERPER_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(key, "")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _mock_free_sources() -> None:
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(200, json=ORCID_BODY)
    )
    respx.get("https://api.openalex.org/authors").mock(
        return_value=httpx.Response(200, json=OPENALEX_BODY)
    )
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={"search": []})
    )
    respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(200, json=GITHUB_SEARCH)
    )
    respx.get(url__regex=r"https://api\\.github\\.com/users/.*").mock(
        return_value=httpx.Response(200, json=GITHUB_PROFILE)
    )
    # Reddit refuses anonymous access, as it does in most real deployments.
    respx.get("https://www.reddit.com/search.json").mock(return_value=httpx.Response(403))


@pytest.fixture
def person_case(db_session):
    created = case_service.create_case(db_session, CaseCreate(name="Recon acceptance"))
    case_service.add_target(
        db_session,
        created.id,
        TargetCreate(
            value=NAME,
            type=TargetType.PERSON,
            context=PersonContextSchema(
                orcid=ORCID,
                organizations=["Example University"],
                known_usernames=["tsamar"],
                city="Delft",
            ),
        ),
    )
    db_session.commit()
    return created


def _target(db_session, case):
    from app.models import Target

    return db_session.scalars(select(Target).where(Target.case_id == case.id)).one()


@respx.mock
def test_the_whole_person_workflow_runs_at_zero_cost(db_session, person_case, zero_cost):
    _mock_free_sources()
    target = _target(db_session, person_case)

    # 1. The free structured collectors run.
    result = InvestigationEngine().run(db_session, person_case.id)
    assert result.findings_created > 0
    runs = {
        run.collector: run
        for run in db_session.scalars(
            select(CollectorRun).where(CollectorRun.case_id == person_case.id)
        )
    }
    assert runs["orcid"].status is RunStatus.SUCCESS
    assert runs["openalex"].status is RunStatus.SUCCESS
    assert runs["github_people"].status is RunStatus.SUCCESS
    # The paid collector is skipped, and that is not a failure.
    assert runs["search"].status is RunStatus.SKIPPED
    assert "SEARCH_PROVIDER" in (runs["search"].error_message or "")
    # A platform refusing anonymous access is skipped with its reason.
    assert runs["reddit"].status is RunStatus.SKIPPED
    assert result.collectors_failed == 0

    # 2. Recon queries are generated from the name and the anchors.
    context = PersonContext(
        orcid=ORCID,
        organizations=("Example University",),
        known_usernames=("tsamar",),
        city="Delft",
    )
    queries = [item.query for item in generate_queries(NAME, context)]
    assert f'"{NAME}" site:linkedin.com' in queries
    assert f'"{NAME}" "Example University"' in queries
    assert f'"{ORCID}"' in queries

    # 3. The investigator imports public results they found themselves.
    recon_import.import_results(
        db_session,
        person_case.id,
        target.id,
        ManualResultImport(
            results=[
                ManualResult(
                    query=f'"{NAME}" site:linkedin.com',
                    url="https://www.linkedin.com/in/timotheous-samar",
                    title=f"{NAME} — Example University",
                    snippet="Researcher at Example University",
                    engine="Google",
                ),
                ManualResult(
                    query=f'"{NAME}" conference',
                    url="https://example.org/conference/speakers",
                    title="Example Conference speakers",
                    engine="Google",
                    image_url="https://example.org/img/speakers.jpg",
                    caption=f"Speakers including {NAME}",
                ),
            ]
        ),
    )
    db_session.commit()

    # 4. Imported URLs are classified, and the image is context evidence.
    findings = list(db_session.scalars(select(Finding).where(Finding.case_id == person_case.id)))
    manual = [f for f in findings if f.kind is FindingKind.MANUAL_SEARCH_RESULT]
    images = [f for f in findings if f.kind is FindingKind.IMAGE_EVIDENCE]
    assert manual and images
    assert manual[0].data["platform"] == "linkedin"
    assert manual[0].data["url_kind"] == "social"
    assert images[0].data["biometric_matching"] is False

    # 5. Re-correlate so the imports become candidates alongside the API ones.
    InvestigationEngine().run(db_session, person_case.id)

    candidates = [
        entity
        for entity in db_session.scalars(
            select(Entity).where(
                Entity.case_id == person_case.id, Entity.type == EntityType.PERSONA
            )
        )
        if entity.canonical_value.startswith("person-candidate:")
    ]
    by_source: dict[str, list[Entity]] = {}
    for candidate in candidates:
        by_source.setdefault(str(candidate.attributes.get("source")), []).append(candidate)
    assert {"orcid", "openalex", "github_people", "manual_search_recon"} <= set(by_source)

    # 6. The ORCID anchor corroborates exactly one of the two same-name records.
    orcid_candidates = by_source["orcid"]
    matched = next(c for c in orcid_candidates if ORCID in c.canonical_value)
    unmatched = next(c for c in orcid_candidates if ORCID not in c.canonical_value)
    assert "orcid" in matched.attributes["corroborated_by"]
    assert unmatched.attributes["corroborated_by"] == []
    assert matched.confidence > unmatched.confidence
    # …and the one that carries a different ORCID is flagged as conflicting.
    assert (
        "orcid" in unmatched.attributes.get("conflicts", [])
        or unmatched.attributes["mismatch_reasons"]
    )

    # 7. Corroborated or not, nothing is auto-merged.
    for candidate in candidates:
        assert candidate.confidence < AUTO_MERGE_THRESHOLD
        assert candidate.attributes["identity_established"] is False

    # 8. Explanations name the source that actually produced each record.
    links = list(
        db_session.scalars(
            select(Relationship).where(
                Relationship.case_id == person_case.id,
                Relationship.type == RelationshipType.REFERENCED_BY,
            )
        )
    )
    explanations = {
        str(link.attributes.get("source")): " ".join(link.confidence_reasons or [])
        for link in links
    }
    assert "ORCID's public registry" in explanations["orcid"]
    assert "OpenAlex" in explanations["openalex"]
    assert "GitHub's public user search" in explanations["github_people"]
    assert "investigator imported" in explanations["manual_search_recon"].lower()
    for source, text in explanations.items():
        if source != "search":
            assert "search provider" not in text.lower(), source

    # 9. A report renders, and distinguishes the evidence classes.
    report = build_report(db_session, person_case.id)
    assert report.counts["findings"] > 0
    assert any("personal name is not an identifier" in line for line in report.limitations)
    classes = {str((link.attributes or {}).get("evidence_class")) for link in links}
    assert {"api_fetched", "investigator_imported"} <= classes


@respx.mock
def test_orcid_and_openalex_agreeing_on_an_orcid_is_a_shared_identifier_not_corroboration(
    db_session, person_case, zero_cost
):
    """OpenAlex takes ORCID iDs *from* ORCID, so their agreement is one value.

    This test asserted the opposite until the lineage model existed, and the
    assertion was wrong: two different hostnames are not two parties. The
    agreement is still recorded — an investigator wants to see it — but it is
    recorded as a shared identifier, with the reason it does not corroborate, and
    it moves no score.
    """
    _mock_free_sources()
    result = InvestigationEngine().run(db_session, person_case.id)

    assert result.corroborations == 0, "dependent lineage must not corroborate"
    candidates = [
        entity
        for entity in db_session.scalars(
            select(Entity).where(
                Entity.case_id == person_case.id, Entity.type == EntityType.PERSONA
            )
        )
        if str(entity.canonical_value).startswith("person-candidate:")
    ]
    assert candidates

    shared = [
        entry
        for entity in candidates
        for entry in ((entity.attributes or {}).get("shared_identifiers") or [])
    ]
    assert shared, "the agreement must still be visible to the investigator"
    agreement = next(entry for entry in shared if entry["identifier"] == "orcid")
    assert set(agreement["sources"]) == {"openalex", "orcid"}
    assert agreement["independence"] == "DEPENDENT"
    assert "takes ORCID values from orcid" in agreement["reason"]

    # And nothing was strengthened: no entity carries a corroboration record.
    for entity in candidates:
        assert not (entity.attributes or {}).get("corroborating_sources")
        assert not (entity.attributes or {}).get("corroboration_reasons")
        assert entity.confidence < AUTO_MERGE_THRESHOLD
