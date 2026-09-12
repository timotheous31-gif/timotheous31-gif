"""Automated search ingestion: the commercial path, and its limits.

The acceptance failure this closes: forty-six well-aimed queries, zero findings,
because a query is a suggestion and nothing came back unless the investigator
pasted it in. When a provider is configured, results must reach the report by
themselves — and must not become stronger evidence for having done so.

Every provider here is a fixture. Nothing in this file contacts a search engine,
and a test asserts the codebase contains no code that could.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConfigurationError
from app.models import Finding
from app.models.enums import FindingKind
from app.services.providers.search import SearchProvider, SearchResult

CANONICAL = "Tabitha Afzal Imdad"
REDUCED = "Tabitha Afzal"
LINKEDIN = "https://www.linkedin.com/in/tabitha-example"
TEAM_PAGE = "https://example.org/team/tabitha-afzal"
ORGANIZATION = "Example International Organization"


class FakeProvider(SearchProvider):
    """A provider whose answers a test writes.

    Results are keyed by query so a test can say "this spelling finds the
    LinkedIn profile and that one finds nothing", which is the whole point of
    having name variants.
    """

    key = "fake"
    display_name = "Fake provider"

    def __init__(self, answers=None, *, available: bool = True, error: Exception | None = None):
        self.answers = answers or {}
        self._available = available
        self.error = error
        self.queries: list[str] = []

    def is_available(self):
        return (True, "") if self._available else (False, "No fake provider key is set")

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        for needle, results in self.answers.items():
            if needle in query:
                return list(results)[:limit]
        return []


def result(url: str, title: str, snippet: str = "", **kwargs) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        rank=kwargs.pop("rank", 1),
        provider="fake",
        **kwargs,
    )


#: The acceptance fixtures, shaped like a real provider's output.
LINKEDIN_RESULT = result(
    LINKEDIN,
    "Tabitha Afzal",
    f"Communications professional at {ORGANIZATION}",
    displayed_url="linkedin.com",
)
TEAM_RESULT = result(
    TEAM_PAGE,
    f"Tabitha Afzal - {ORGANIZATION}",
    "Communications ...",
    displayed_url="example.org",
)
IMAGE_RESULT = result(
    TEAM_PAGE,
    "Tabitha Afzal",
    "Team photograph",
    image_url="https://example.org/media/tabitha.jpg",
)


async def _person(api_client, case_id, *, name: str = CANONICAL, context: dict | None = None):
    body: dict = {"value": name, "type": "PERSON"}
    if context:
        body["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _ingest(case_id, target_id, provider):
    """Run ingestion and promotion the way the engine does."""
    from app.core.db import get_session_factory
    from app.services.promotion import promote_finding
    from app.services.search_ingest import search_target

    with get_session_factory()() as session:
        report = await search_target(
            session,
            case_id=_uuid.UUID(case_id),
            target_id=_uuid.UUID(target_id),
            provider=provider,
        )
        from app.models import Target

        target = session.get(Target, _uuid.UUID(target_id))
        for finding_id in report.findings:
            promote_finding(
                session,
                case_id=_uuid.UUID(case_id),
                finding=session.get(Finding, finding_id),
                target=target,
            )
        session.commit()
    return report


def _findings(case_id):
    from app.core.db import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(select(Finding).where(Finding.case_id == _uuid.UUID(case_id))))


# --------------------------------------------------------------- the contract


async def test_an_unconfigured_provider_reports_itself_rather_than_finding_nothing(
    api_client, case_id
):
    """The distinction the whole coverage model rests on."""
    target_id = await _person(api_client, case_id)
    report = await _ingest(case_id, target_id, FakeProvider(available=False))
    assert report.configured is False
    assert report.queries_run == 0
    assert report.reason
    assert _findings(case_id) == []


async def test_a_provider_returning_nothing_is_not_a_provider_that_was_not_run(api_client, case_id):
    target_id = await _person(api_client, case_id)
    report = await _ingest(case_id, target_id, FakeProvider({}))
    assert report.configured is True
    assert report.queries_run > 0
    assert report.results_stored == 0


async def test_a_social_result_becomes_a_person_candidate(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    findings = _findings(case_id)
    assert findings
    candidate = next(item for item in findings if item.data["url"].endswith("tabitha-example"))
    assert candidate.kind is FindingKind.PERSON_CANDIDATE
    assert candidate.data["platform"] == "linkedin"
    assert candidate.data["handle"] == "tabitha-example"


async def test_an_organisation_page_becomes_a_web_result(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [TEAM_RESULT]}))
    finding = _findings(case_id)[0]
    assert finding.kind is FindingKind.SEARCH_RESULT
    assert finding.data["url"] == TEAM_PAGE


async def test_a_document_result_becomes_a_document(api_client, case_id):
    target_id = await _person(api_client, case_id)
    pdf = result("https://example.org/reports/annual.pdf", "Annual report")
    await _ingest(case_id, target_id, FakeProvider({CANONICAL: [pdf]}))
    assert _findings(case_id)[0].kind is FindingKind.PUBLIC_DOCUMENT


async def test_an_image_result_becomes_image_evidence(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [IMAGE_RESULT]}))
    finding = _findings(case_id)[0]
    assert finding.kind is FindingKind.IMAGE_EVIDENCE
    assert finding.data["image_url"] == "https://example.org/media/tabitha.jpg"
    assert finding.data["biometric_matching"] is False

    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert images and images[0]["fetch_state"] == "REFERENCE_ONLY"


async def test_a_provider_failure_is_recorded_and_does_not_end_the_run(api_client, case_id):
    target_id = await _person(api_client, case_id)
    report = await _ingest(
        case_id, target_id, FakeProvider(error=ConfigurationError("Serper returned HTTP 500"))
    )
    assert report.configured is True
    assert report.failures
    assert report.queries_run == 0
    assert "HTTP 500" in report.failures[0]["error"]


async def test_a_rate_limited_provider_is_a_failure_not_an_absence(api_client, case_id):
    from app.core.errors import RateLimitExceeded

    target_id = await _person(api_client, case_id)
    report = await _ingest(
        case_id, target_id, FakeProvider(error=RateLimitExceeded("fake rate-limited"))
    )
    assert report.failures
    assert report.results_stored == 0
    assert _findings(case_id) == []


async def test_a_malformed_result_is_rejected_rather_than_stored(api_client, case_id):
    target_id = await _person(api_client, case_id)
    broken = [
        result("", "No URL at all"),
        result("javascript:alert(1)", "Not a web address"),
        result("http://169.254.169.254/latest/meta-data/", "Cloud metadata"),
        result("http://127.0.0.1/admin", "Loopback"),
    ]
    report = await _ingest(case_id, target_id, FakeProvider({CANONICAL: broken}))
    assert report.results_stored == 0
    # Several queries return the same bad set, so each is rejected more than
    # once. What matters is that none of them was ever stored.
    assert report.rejected_urls >= len(broken)
    assert _findings(case_id) == []


# ----------------------------------------------- dedupe without double-counting


async def test_the_same_url_from_two_queries_is_one_finding(api_client, case_id):
    target_id = await _person(api_client, case_id)
    # Both the canonical and the reduced spelling return the same profile.
    provider = FakeProvider({"Tabitha": [LINKEDIN_RESULT]})
    report = await _ingest(case_id, target_id, provider)
    matches = [item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")]
    assert len(matches) == 1
    assert report.duplicates >= 1


async def test_a_repeated_url_records_the_extra_search_without_rescoring(api_client, case_id):
    target_id = await _person(api_client, case_id)
    provider = FakeProvider({"Tabitha": [LINKEDIN_RESULT]})
    await _ingest(case_id, target_id, provider)
    finding = next(
        item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")
    )
    searches = finding.data["searches"]
    assert len(searches) > 1, "every search that found the page must be recorded"
    # One page, one score. Four queries reaching it is not four sources agreeing.
    assert finding.confidence <= 0.30


async def test_the_same_url_found_through_two_variants_keeps_both_provenances(api_client, case_id):
    target_id = await _person(api_client, case_id)
    provider = FakeProvider({"Tabitha": [LINKEDIN_RESULT]})
    await _ingest(case_id, target_id, provider)
    finding = next(
        item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")
    )
    variants = {entry["search_variant"] for entry in finding.data["searches"]}
    assert len(variants) > 1, "the spellings that found it must all be recorded"


# ------------------------------------------------ a result is not identity proof


async def test_a_reduced_name_result_stays_weak_without_corroboration(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    finding = next(
        item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")
    )
    assert finding.data["name_variant_type"] == "TOKEN_REDUCED_VARIANT"
    assert finding.confidence <= 0.20, "a shorter name alone is a lead, not a match"
    assert finding.data["corroborated_by"] == []


async def test_a_supplied_organisation_strengthens_the_same_result(api_client, case_id):
    """The independent signal the brief asks for, and nothing else changed."""
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [TEAM_RESULT]}))
    finding = _findings(case_id)[0]
    assert "affiliation" in finding.data["corroborated_by"]
    assert finding.confidence > 0.20


async def test_being_returned_by_a_search_engine_earns_nothing(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({CANONICAL: [TEAM_RESULT]}))
    finding = _findings(case_id)[0]
    assert any("says nothing" in reason for reason in finding.data["mismatch_reasons"])
    assert finding.confidence <= 0.30


async def test_provenance_records_the_query_variant_and_family(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    finding = next(
        item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")
    )
    search = finding.data["searches"][0]
    assert search["query"]
    assert search["search_variant"]
    assert search["variant_type"]
    assert search["query_family"]
    assert search["provider"] == "fake"
    assert finding.data["canonical_target"] == CANONICAL


async def test_the_target_name_is_never_replaced_by_a_variant(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    body = (await api_client.get(f"/api/v1/cases/{case_id}/targets")).json()
    target = (body["items"] if isinstance(body, dict) else body)[0]
    assert target["attributes"]["display_name"] == CANONICAL
    assert target["normalized_value"] == CANONICAL.lower()


# ------------------------------------------------ two people, one short name


async def test_an_unrelated_person_with_the_same_short_name_stays_separate(api_client, case_id):
    """The scenario the brief names: same reduced spelling, different context."""
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    unrelated = result(
        "https://www.linkedin.com/in/tabitha-other",
        "Tabitha Afzal",
        "Veterinary surgeon at Example Animal Clinic",
    )
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [TEAM_RESULT, unrelated]}))

    findings = {item.data["url"]: item for item in _findings(case_id)}
    assert len(findings) == 2, "two pages are two candidates, never one"
    ours = findings[TEAM_PAGE]
    theirs = findings["https://www.linkedin.com/in/tabitha-other"]
    assert "affiliation" in ours.data["corroborated_by"]
    assert theirs.data["corroborated_by"] == []
    assert theirs.confidence < ours.confidence

    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    other = next(item for item in profiles if "tabitha-other" in item["profile_url"])
    assert other["confidence"] <= 0.20


async def test_a_conflicting_organisation_is_surfaced_as_a_reason_against(api_client, case_id):
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    conflicting = result(
        TEAM_PAGE.replace("tabitha-afzal", "someone-else"),
        "Tabitha Afzal - Example Animal Clinic",
        "Veterinary surgeon",
    )
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [conflicting]}))
    finding = _findings(case_id)[0]
    assert finding.data["corroborated_by"] == []
    assert finding.data["mismatch_reasons"]


# --------------------------------------------------------- reaching the report


async def test_an_automatic_result_reaches_the_report(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(
        case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT], CANONICAL: [TEAM_RESULT]})
    )
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "tabitha-example" in markdown
    assert "## Public profiles and contacts" in markdown
    assert "## Source coverage" in markdown


async def test_the_report_names_the_variant_that_found_a_profile(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    candidate = next(
        item for item in report["findings"] if "tabitha-example" in str(item["data"].get("url"))
    )
    assert candidate["data"]["name_variant_type"] == "TOKEN_REDUCED_VARIANT"
    assert candidate["data"]["searches"]


async def test_a_promoted_profile_from_a_search_result_shows_its_discovery_method(
    api_client, case_id
):
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert profiles
    assert profiles[0]["discovery_method"] == "provider_search"


@pytest.mark.parametrize("spelling", [CANONICAL, REDUCED])
async def test_every_spelling_in_the_plan_is_actually_searched(api_client, case_id, spelling):
    target_id = await _person(api_client, case_id)
    provider = FakeProvider({})
    await _ingest(case_id, target_id, provider)
    assert any(spelling in query for query in provider.queries), f"{spelling!r} was never searched"


async def test_a_promoted_profile_is_scored_on_the_same_evidence_as_its_finding(
    api_client, case_id
):
    """The PR #9 invariant, arriving by a new route.

    The finding for a search result was scored on the affiliation its page text
    matched; the promoted profile was scored on the URL alone. The two then
    disagreed about the same evidence — 0.54 against 0.08 — and a report showed
    both.
    """
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))

    finding = next(
        item for item in _findings(case_id) if item.data["url"].endswith("tabitha-example")
    )
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    profile = next(item for item in profiles if "tabitha-example" in item["profile_url"])

    assert finding.data["corroborated_by"] == ["affiliation"]
    assert profile["confidence"] == pytest.approx(finding.confidence, abs=1e-9)
    assert set(profile["corroborated_by"]) == set(finding.data["corroborated_by"])


async def test_an_uncorroborated_profile_is_not_lifted_by_a_sibling_result(api_client, case_id):
    """One result's corroboration must not leak onto another page."""
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    unrelated = result(
        "https://www.linkedin.com/in/tabitha-other",
        "Tabitha Afzal",
        "Veterinary surgeon at Example Animal Clinic",
    )
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT, unrelated]}))

    profiles = {
        item["profile_url"]: item
        for item in (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    }
    ours = profiles["https://www.linkedin.com/in/tabitha-example"]
    theirs = profiles["https://www.linkedin.com/in/tabitha-other"]
    assert ours["confidence"] > theirs["confidence"]
    assert theirs["corroborated_by"] == []
    assert theirs["confidence"] <= 0.20


# ------------------------------------------- rescoring a page already stored


def _set_context(target_id, context: dict) -> None:
    """Change the anchors on a target, the way an investigator editing it does."""
    from app.core.db import get_session_factory
    from app.models import Target

    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        assert target is not None
        target.attributes = {**dict(target.attributes or {}), "context": context}
        session.commit()


async def test_a_rerun_with_a_new_anchor_rescores_a_page_already_stored(api_client, case_id):
    """The stale-correlation defect, arriving by the provider-search route.

    PR #9 fixed this on the promoted profile: a correlation is a pure function of
    the page's content and the anchors currently on the target, so an older
    answer is simply wrong once the anchors change. Ingestion's duplicate branch
    recorded the extra search and returned, which left the finding scored as
    though the employer had never been supplied — and ``promote_finding`` then
    read that stale finding, so both halves agreed on the wrong number.
    """
    target_id = await _person(api_client, case_id)
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    first = _findings(case_id)[0]
    assert first.data["corroborated_by"] == []
    before = first.confidence

    # The investigator now supplies the employer the snippet already named.
    _set_context(target_id, {"organizations": [ORGANIZATION]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))

    findings = _findings(case_id)
    assert len(findings) == 1, "a rerun must refresh the page, not store it twice"
    after = findings[0]
    assert after.confidence > before, "the supplied anchor must reach the stored finding"
    assert after.data["corroborated_by"] == ["affiliation"]

    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    profile = next(item for item in profiles if "tabitha-example" in item["profile_url"])
    assert profile["confidence"] == pytest.approx(
        after.confidence
    ), "the finding and its promoted profile must never contradict one another"
    assert profile["corroborated_by"] == ["affiliation"]


async def test_a_rerun_that_learns_nothing_new_leaves_the_score_alone(api_client, case_id):
    """The other half: a refresh is not a reason for a number to drift."""
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    first = _findings(case_id)[0].confidence
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    findings = _findings(case_id)
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(first)


async def test_a_page_disagreeing_with_the_current_employer_is_recorded_as_a_conflict(
    api_client, case_id
):
    """Conflict detection is inherited, not reimplemented.

    Ingestion used to assemble its own signal set, so ``_assess_conflicts`` — the
    shared function that records where a source positively disagrees with an
    anchor — never ran on this path. It now scores through
    ``app.collectors.person.assess`` like every other source, and a page seen to
    name one employer while the target carries another says so.

    The two employer names share no distinctive word on purpose: names that do
    share one are a *match*, not a conflict, which is what
    ``GENERIC_AFFILIATION_TERMS`` exists to get right.
    """
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    assert _findings(case_id)[0].data["conflicts"] == []

    _set_context(target_id, {"organizations": ["Riverbend Veterinary Clinic"]})
    await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    finding = _findings(case_id)[0]
    assert finding.data["conflicts"] == ["affiliation"]
    assert finding.data["corroborated_by"] == []
    assert any(ORGANIZATION in reason for reason in finding.data["mismatch_reasons"])


async def test_the_ingest_report_counts_the_image_queries_it_actually_ran(api_client, case_id):
    """So the report's image channel can only claim an absence it verified.

    The count matters because the query budget is finite: a staged plan whose
    first twelve queries are all name and anchor work issues no image query at
    all, and the report must then say the channel is unsearched rather than
    empty.
    """
    target_id = await _person(api_client, case_id, context={"organizations": [ORGANIZATION]})
    report = await _ingest(case_id, target_id, FakeProvider({REDUCED: [LINKEDIN_RESULT]}))
    assert report.queries_run > 0
    assert 0 < report.image_queries_run <= report.queries_run
    assert report.to_dict()["image_queries_run"] == report.image_queries_run

    unconfigured = await _ingest(case_id, target_id, FakeProvider(available=False))
    assert unconfigured.image_queries_run == 0
