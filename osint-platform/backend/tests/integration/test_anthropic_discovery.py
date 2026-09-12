"""The Anthropic channel end to end: ingestion, lineage, enrichment, accounting.

Everything here goes through the real provider against fixture responses built to
the shapes Anthropic documents, and then through the real ingestion pipeline into
real findings and a real report. Nothing contacts Anthropic.

The properties under test are the ones that would be expensive to discover in
production:

* a description the provider never returned does not appear anywhere;
* a page reached twice is one source, not two agreeing ones;
* two search channels reaching the same URL never amplify each other;
* a name-only result is never auto-confirmed;
* enrichment is capped, policy-checked and routed through the one guarded client;
* the opaque blob and the credential reach no finding, no report and no API
  payload;
* and the zero-cost path is untouched by any of it.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.settings import Settings
from app.correlation.confidence import AUTO_MERGE_THRESHOLD, POSSIBLE_MATCH_THRESHOLD
from app.correlation.lineage import RELAY_SOURCES, Independence, independence
from app.models import Case, Finding, Target
from app.models.enums import TargetType
from app.reporting.coverage import CoverageState
from app.reporting.model import build_report
from app.services.providers.anthropic_web_search import AnthropicWebSearchProvider
from app.services.search_ingest import search_target

pytestmark = pytest.mark.anyio

ENDPOINT = "https://api.anthropic.com/v1/messages"
KEY = "sk-ant-test-DO-NOT-USE-0000"
BLOB = "EqgfCioIARgBIiQ3YTAwMjY1Mi1mZjM5LTQ1NGUtODgxNC1kNjNjNTk1ZWI3Y"

CANONICAL = "Tabitha Afzal Imdad"
REDUCED = "Tabitha Afzal"
ORGANIZATION = "Example International Organization"
PROFILE = "https://www.linkedin.com/in/tabitha-example"
TEAM = "https://example.org/team/tabitha-afzal"
DECOY = "https://example.net/blog/tabitha-afzal-the-cyclist"


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "environment": "test",
        "search_provider": "anthropic_web_search",
        "anthropic_api_key": KEY,
        # Off by default in these tests; the enrichment tests turn it on so the
        # fetch is always a deliberate part of the scenario under test.
        "result_enrichment_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _row(url: str, title: str, page_age: str = "April 30, 2025") -> dict:
    return {
        "type": "web_search_result",
        "url": url,
        "title": title,
        "page_age": page_age,
        "encrypted_content": BLOB,
    }


def _payload(searches, *, requests: int | None = None, start: int = 1) -> dict:
    content: list[dict] = []
    for index, (query, rows) in enumerate(searches, start=start):
        call_id = f"srvtoolu_{index:02d}"
        content.append(
            {
                "type": "server_tool_use",
                "id": call_id,
                "name": "web_search",
                "input": {"query": query},
            }
        )
        content.append({"type": "web_search_tool_result", "tool_use_id": call_id, "content": rows})
    content.append({"type": "text", "text": "DONE"})
    return {
        "role": "assistant",
        "content": content,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 5000,
            "output_tokens": 100,
            "server_tool_use": {
                "web_search_requests": len(searches) if requests is None else requests
            },
        },
    }


def _error_payload(code: str) -> dict:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_01",
                "name": "web_search",
                "input": {"query": "tabitha afzal"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_01",
                "content": {"type": "web_search_tool_result_error", "error_code": code},
            },
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {}},
    }


def _case_with_person(session, *, context: dict | None = None):
    case = Case(name="Anthropic discovery channel")
    session.add(case)
    session.flush()
    target = Target(
        case_id=case.id,
        type=TargetType.PERSON,
        raw_input=CANONICAL,
        normalized_value=CANONICAL.lower(),
        attributes={"display_name": CANONICAL, "context": context or {}},
    )
    session.add(target)
    session.flush()
    return case, target


async def _ingest(session, case, target, settings):
    provider = AnthropicWebSearchProvider(settings)
    report = await search_target(
        session,
        case_id=case.id,
        target_id=target.id,
        provider=provider,
        settings=settings,
    )
    session.flush()
    return report


def _findings(session, case_id) -> list[Finding]:
    return list(session.scalars(select(Finding).where(Finding.case_id == case_id)))


def _finding_for(session, case_id, url: str) -> Finding | None:
    return next(
        (item for item in _findings(session, case_id) if url in str(item.source_url or "")),
        None,
    )


# -------------------------------------------------------- the missing snippet


@respx.mock
async def test_a_result_with_no_description_is_stored_as_a_low_context_candidate(
    db_session, mock_http
):
    """No invented snippet, and the record says the field was never returned."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("tabitha afzal", [_row(TEAM, REDUCED)])]))
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())

    assert report.results_stored == 1
    assert report.low_context_results == 1
    finding = _finding_for(db_session, case.id, TEAM)
    assert finding is not None
    data = finding.data
    # ``None``, not ``""``: "this provider publishes no description" is a
    # different fact from "it published an empty one", and the report shows both.
    assert data["snippet"] is None
    assert data["snippet_available"] is False
    assert data["page_age"] == "April 30, 2025"
    # The summary states the absence rather than papering over it with the title.
    assert "no page description" in finding.summary
    assert finding.title not in (finding.summary or "")


@respx.mock
async def test_no_anchor_is_claimed_from_a_result_that_carried_no_text(db_session, mock_http):
    """The honest cost of the missing snippet, made explicit in the record."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("tabitha afzal", [_row(TEAM, REDUCED)])]))
    )
    case, target = _case_with_person(
        db_session, context={"organizations": [ORGANIZATION], "places": ["Cape Town"]}
    )
    await _ingest(db_session, case, target, _settings())
    finding = _finding_for(db_session, case.id, TEAM)
    assert finding is not None
    assert finding.data["affiliations"] == []
    assert finding.data["locations"] == []
    assert any(
        "only thing linking it to the subject is the name" in reason
        for reason in finding.data["mismatch_reasons"]
    )


@respx.mock
async def test_a_name_only_result_is_never_auto_confirmed(db_session, mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [("tabitha afzal imdad", [_row(TEAM, CANONICAL), _row(DECOY, CANONICAL)])]
            ),
        )
    )
    case, target = _case_with_person(db_session)
    await _ingest(db_session, case, target, _settings())
    for finding in _findings(db_session, case.id):
        assert finding.confidence < AUTO_MERGE_THRESHOLD
        assert finding.confidence < POSSIBLE_MATCH_THRESHOLD


# ------------------------------------------------------------ one page, twice


@respx.mock
async def test_the_same_url_from_two_searches_is_one_finding_with_two_provenance_records(
    db_session, mock_http
):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    ("tabitha afzal imdad", [_row(TEAM, REDUCED)]),
                    ("tabitha afzal example org", [_row(TEAM, REDUCED)]),
                ]
            ),
        )
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())

    assert report.results_seen == 2
    assert report.results_stored == 1
    assert report.duplicates == 1
    findings = [item for item in _findings(db_session, case.id) if TEAM in str(item.source_url)]
    assert len(findings) == 1
    searches = findings[0].data["searches"]
    assert len(searches) == 2
    assert {entry["executed_query"] for entry in searches} == {
        "tabitha afzal imdad",
        "tabitha afzal example org",
    }
    # Two routes to one page. Provenance about the search, not agreement about
    # the subject — so the score is untouched by the second route.
    assert all(entry["query_executed_as_planned"] is False for entry in searches)


@respx.mock
async def test_planned_and_executed_queries_are_recorded_separately(db_session, mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json=_payload([("an entirely different search", [_row(TEAM, REDUCED)])])
        )
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())

    assert report.queries_planned > 0
    assert report.queries_as_planned is False
    assert report.executed_queries == ["an entirely different search"]
    # The plan is not a record of work, and the ledger counts the provider's own
    # search count rather than the number of queries the platform hoped for.
    assert report.queries_run == 1
    assert report.queries_run != report.queries_planned

    entry = _finding_for(db_session, case.id, TEAM).data["searches"][0]
    assert entry["executed_query"] == "an entirely different search"
    assert entry["planned_query"] is None
    assert entry["query_executed_as_planned"] is False
    assert entry["server_tool_use_id"] == "srvtoolu_01"
    assert entry["tool_result_id"] == "srvtoolu_01"


# ----------------------------------------------------------------- lineage


def test_the_channel_is_a_relay_source_and_amplifies_nothing():
    assert "anthropic_web_search" in RELAY_SOURCES
    verdict = independence("anthropic_web_search", "provider_search", "orcid")
    assert verdict.status is Independence.UNKNOWN
    assert verdict.amplifies is False
    # And against a source that *is* authoritative, it still cannot corroborate:
    # a relay originates nothing, so there is no second party.
    assert independence("anthropic_web_search", "orcid", "orcid").amplifies is False


@respx.mock
async def test_the_retrieval_channel_and_the_claim_origin_are_recorded_apart(db_session, mock_http):
    """Anthropic never names its index, so the channel is never named as one."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(TEAM, REDUCED)])]))
    )
    case, target = _case_with_person(db_session)
    await _ingest(db_session, case, target, _settings())
    data = _finding_for(db_session, case.id, TEAM).data
    assert data["retrieval_channel"] == "anthropic_web_search"
    assert data["claim_origin"] == TEAM
    assert data["claim_origin_domain"] == "example.org"
    blob = str(data).lower()
    for engine in ("google", "bing", "brave", "duckduckgo"):
        assert engine not in blob


# -------------------------------------------------------------- enrichment


@respx.mock
async def test_enrichment_reads_a_high_value_page_and_correlates_against_its_text(
    db_session, mock_http
):
    """The conservative answer to the missing snippet: read the page, not guess it."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json=_payload([("tabitha afzal imdad", [_row(TEAM, CANONICAL)])])
        )
    )
    respx.get("https://example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get(TEAM).mock(
        return_value=httpx.Response(
            200,
            html=(
                f"<html><head><title>{CANONICAL}</title></head><body>"
                f"<p>{CANONICAL} is a communications officer at {ORGANIZATION}.</p>"
                f"</body></html>"
            ),
        )
    )
    case, target = _case_with_person(db_session, context={"organizations": [ORGANIZATION]})
    report = await _ingest(db_session, case, target, _settings(result_enrichment_enabled=True))

    assert report.enrichment["fetches_used"] == 1
    finding = _finding_for(db_session, case.id, TEAM)
    assert finding.data["enriched"] is True
    assert finding.data["page_excerpt"]["state"] == "fetched"
    assert ORGANIZATION in finding.data["page_excerpt"]["excerpt"]
    # The supplied anchor was observed in the page's own public text, so the
    # affiliation rule fires — on real text, not on a synthesised description.
    assert finding.data["affiliations"] == [ORGANIZATION]
    assert finding.confidence >= POSSIBLE_MATCH_THRESHOLD
    assert any("fetched directly" in reason for reason in finding.data["match_reasons"])
    # And still not an identification.
    assert finding.confidence < AUTO_MERGE_THRESHOLD


@respx.mock
async def test_enrichment_is_capped_per_investigation(db_session, mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    (
                        "tabitha afzal imdad",
                        [
                            _row(f"https://example.org/team/{slug}", CANONICAL)
                            for slug in ("a", "b", "c", "d", "e")
                        ],
                    )
                ]
            ),
        )
    )
    respx.get("https://example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    page = respx.get(url__regex=r"https://example\.org/team/[a-e]$").mock(
        return_value=httpx.Response(200, html=f"<html><body><p>{CANONICAL}</p></body></html>")
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(
        db_session,
        case,
        target,
        _settings(result_enrichment_enabled=True, max_result_enrichments_per_investigation=2),
    )
    assert report.enrichment["limit"] == 2
    assert report.enrichment["fetches_used"] == 2
    assert len(page.calls) == 2
    # The pages not read are recorded as not read, with the reason.
    states = {item["state"] for item in report.enrichment["outcomes"]}
    assert "fetched" in states
    assert "skipped_budget" in states


@respx.mock
async def test_enrichment_refuses_a_platform_that_blocks_server_side_fetching(
    db_session, mock_http
):
    """A block is never read as an absence, and no request is made."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(PROFILE, CANONICAL)])]))
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings(result_enrichment_enabled=True))
    assert report.enrichment["fetches_used"] == 0
    outcome = next(item for item in report.enrichment["outcomes"] if PROFILE in item["url"])
    assert outcome["state"] == "skipped_not_fetchable"
    assert outcome["detail"]
    finding = _finding_for(db_session, case.id, PROFILE)
    assert finding.data["enriched"] is False


@respx.mock
async def test_enrichment_honours_robots_txt(db_session, mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(TEAM, CANONICAL)])]))
    )
    respx.get("https://example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /team\n")
    )
    fetched = respx.get(TEAM).mock(return_value=httpx.Response(200, html="<html></html>"))
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings(result_enrichment_enabled=True))
    assert not fetched.called
    outcome = next(item for item in report.enrichment["outcomes"] if TEAM in item["url"])
    assert outcome["state"] == "skipped_policy"


@respx.mock
async def test_an_unsafe_result_url_is_rejected_before_anything_is_stored_or_fetched(
    db_session, mock_http
):
    """Provider output is untrusted input, and the SSRF guard is the gate.

    ``metadata.google.internal`` resolves, in this suite, to the link-local
    address the guard blocks — so the guard is exercised on the name rather than
    short-circuited by a literal.
    """
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    (
                        "q",
                        [
                            _row("http://metadata.google.internal/computeMetadata/v1/", "Metadata"),
                            _row("file:///etc/passwd", "Local file"),
                            _row("https://user:pass@example.org/team/x", "Credentials in URL"),
                            _row(TEAM, CANONICAL),
                        ],
                    )
                ]
            ),
        )
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings(result_enrichment_enabled=True))
    assert report.rejected_urls == 3
    assert report.results_stored == 1
    stored = {str(item.source_url) for item in _findings(db_session, case.id)}
    assert stored == {TEAM}
    assert not any("metadata" in url for url in stored)


# --------------------------------------------- in-200 errors and coverage


@respx.mock
@pytest.mark.parametrize(
    ("code", "expected_state"),
    [
        ("max_uses_exceeded", CoverageState.SKIPPED),
        ("too_many_requests", CoverageState.SKIPPED),
        ("unavailable", CoverageState.SKIPPED),
    ],
)
async def test_an_error_inside_http_200_never_becomes_no_match_returned(
    db_session, mock_http, code, expected_state
):
    """The single most dangerous misreading this channel could produce."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_error_payload(code)))
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())

    assert report.blocked is True
    assert report.queries_run == 0
    assert report.results_stored == 0

    from app.reporting.coverage import web_search_coverage

    item = web_search_coverage(
        provider=report.provider,
        configured=report.configured,
        queries_run=report.queries_run,
        results_stored=report.results_stored,
        reason=report.reason,
        failures=len(report.failures),
        blocked=report.blocked,
        outcome=report.outcome,
        queries_as_planned=report.queries_as_planned,
    )
    assert item.state is expected_state
    assert item.state is not CoverageState.NO_MATCH_RETURNED


@respx.mock
async def test_a_search_that_matched_nothing_is_a_real_absence(db_session, mock_http):
    """The other half of the same distinction: an empty list is evidence."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("tabitha afzal", [])]))
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())
    assert report.blocked is False
    assert report.queries_run == 1
    assert report.results_stored == 0

    from app.reporting.coverage import web_search_coverage

    item = web_search_coverage(
        provider=report.provider,
        configured=report.configured,
        queries_run=report.queries_run,
        results_stored=report.results_stored,
        blocked=report.blocked,
        outcome=report.outcome,
        queries_as_planned=report.queries_as_planned,
    )
    assert item.state is CoverageState.NO_MATCH_RETURNED


# ------------------------------------------------------- accounting & leaks


@respx.mock
async def test_cost_accounting_reaches_the_report_labelled_as_an_estimate(db_session, mock_http):
    """From the provider's own count, through the execution record, into the report.

    The job's ``search_ingest`` result is written here exactly as the engine writes
    it, because that is the handover being tested: the report must describe what
    the execution *recorded*, not re-derive it from whatever the settings say at
    render time. The engine's own writing of that key is covered by
    ``test_execution_reports.py``.
    """
    from app.models import Job
    from app.models.enums import JobState

    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [("a", [_row(TEAM, CANONICAL)]), ("b", [_row(DECOY, CANONICAL)])], requests=3
            ),
        )
    )
    case, target = _case_with_person(db_session)
    ingest = await _ingest(db_session, case, target, _settings(anthropic_web_search_max_uses=4))
    job = Job(
        case_id=case.id,
        state=JobState.COMPLETE,
        params={},
        result={"search_ingest": ingest.to_dict()},
    )
    db_session.add(job)
    db_session.commit()

    report = build_report(db_session, case.id)
    channel = report.search_channel
    assert channel is not None
    assert channel.provider == "anthropic_web_search"
    assert channel.searches_executed == 3
    assert channel.search_budget == 4
    assert channel.budget_label == "3 / 4"
    assert channel.estimated_search_cost_usd == 0.03
    assert "estimated" in channel.cost_label
    payload = channel.to_dict()
    assert payload["cost"]["is_estimate"] is True
    assert payload["cost"]["is_invoice"] is False
    assert payload["cost"]["token_cost_included"] is False
    assert payload["queries_as_planned"] is False
    assert "chose its own queries" in payload["query_attribution"]

    from app.reporting.renderers import render_markdown

    markdown = render_markdown(report)
    assert "Public web search channel" in markdown
    assert "Estimated search-tool cost" in markdown
    assert "Token cost: **not included**" in markdown


@respx.mock
async def test_neither_the_blob_nor_the_credential_reaches_a_finding_a_report_or_the_api(
    db_session, mock_http
):
    """One assertion per surface, because each is a separate way to leak."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(TEAM, CANONICAL)])]))
    )
    case, target = _case_with_person(db_session)
    report = await _ingest(db_session, case, target, _settings())
    db_session.commit()

    assert BLOB not in str(report.to_dict())
    assert KEY not in str(report.to_dict())

    for finding in _findings(db_session, case.id):
        assert BLOB not in str(finding.data)
        assert KEY not in str(finding.data)
        for artefact in finding.evidence or []:
            assert BLOB not in str(artefact.excerpt or "")
            assert KEY not in str(artefact.excerpt or "")

    from app.reporting.renderers import render_json, render_markdown

    model = build_report(db_session, case.id)
    for rendered in (render_json(model), render_markdown(model)):
        assert BLOB not in rendered
        assert KEY not in rendered


# ---------------------------------------------------------- the $0 default


async def test_the_zero_cost_default_needs_no_anthropic_credential(db_session):
    """No provider, no credential, no paid call, and the reason is recorded."""
    settings = Settings(_env_file=None, environment="test")
    assert settings.search_provider == "none"
    assert settings.anthropic_api_key is None

    from app.services.providers.search import get_search_provider

    provider = get_search_provider(settings)
    assert provider.key == "none"

    case, target = _case_with_person(db_session)
    report = await search_target(
        db_session,
        case_id=case.id,
        target_id=target.id,
        provider=provider,
        settings=settings,
    )
    assert report.configured is False
    assert report.queries_run == 0
    assert report.reason
    assert "manual search workflow keep working" in report.reason
    assert _findings(db_session, case.id) == []


async def test_selecting_anthropic_without_a_key_does_not_silently_use_another_provider(
    db_session,
):
    """Unavailable means unavailable. No paid substitute, ever."""
    settings = Settings(
        _env_file=None,
        environment="test",
        search_provider="anthropic_web_search",
        brave_api_key="a-brave-key-that-must-not-be-used",
    )
    from app.services.providers.search import get_search_provider

    provider = get_search_provider(settings)
    assert provider.key == "anthropic_web_search"

    case, target = _case_with_person(db_session)
    report = await search_target(
        db_session,
        case_id=case.id,
        target_id=target.id,
        provider=provider,
        settings=settings,
    )
    assert report.configured is False
    assert report.provider == "anthropic_web_search"
    assert "ANTHROPIC_API_KEY" in report.reason
    assert _findings(db_session, case.id) == []


async def test_google_wss_cannot_be_activated_by_configuration_alone(db_session):
    settings = Settings(
        _env_file=None,
        environment="test",
        search_provider="google_wss",
        google_wss_api_key="k",
        google_wss_client_id="c",
    )
    from app.services.providers.search import get_search_provider

    case, target = _case_with_person(db_session)
    report = await search_target(
        db_session,
        case_id=case.id,
        target_id=target.id,
        provider=get_search_provider(settings),
        settings=settings,
    )
    assert report.configured is False
    assert "PENDING_PARTNER_ACCESS" in report.reason
    assert _findings(db_session, case.id) == []
