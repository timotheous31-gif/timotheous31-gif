"""Discovery benchmark: does the channel actually find things, and only true things.

The standard set in review: *a change is not successful merely because it
generates better queries.* So each case below states a small ground truth — pages
the subject is known to have, and pages known to be about a different person of
the same name — and the run is measured against it.

**Every fixture here is synthetic.** The two subject names are the regression
shapes the product owner asked for, and the pages, organisations, places and page
text attached to them are invented and live on RFC 2606 documentation domains.
Nothing in this file asserts a fact about a living person, and nothing in it was
retrieved from the public web. A benchmark whose ground truth is a real person's
real footprint would be asserting things about that person, which is not a
benchmark's business.

Both providers are measured on the same cases, because the interesting result is
the *difference*: a deterministic provider that returns descriptions reaches the
anchor rules from the result set alone, while Anthropic's channel returns URLs and
titles and has to read a capped number of pages to get there. The numbers below are
the honest cost of that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.correlation.confidence import POSSIBLE_MATCH_THRESHOLD
from app.models import Case, Target
from app.models.enums import TargetType
from app.services.benchmark import BenchmarkMetrics, measure, render
from app.services.providers.search import SearchProvider, SearchResult
from app.services.search_ingest import search_target

pytestmark = pytest.mark.anyio

ENDPOINT = "https://api.anthropic.com/v1/messages"
KEY = "sk-ant-test-DO-NOT-USE-0000"


@dataclass(slots=True)
class BenchmarkCase:
    """One benchmark case: a subject, a ground truth, and canned provider output."""

    label: str
    canonical: str
    context: dict
    #: Keyed by a substring of the query, as a provider's index would behave.
    answers: dict[str, list[tuple[str, str, str]]]
    known_urls: list[str]
    decoy_urls: list[str]
    #: Public page text for the pages enrichment is allowed to read.
    pages: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------- the cases

TABITHA = BenchmarkCase(
    label="Tabitha Afzal (synthetic)",
    canonical="Tabitha Afzal Imdad",
    context={"organizations": ["Example International Organization"], "places": ["Cape Town"]},
    answers={
        "Tabitha Afzal": [
            (
                "https://www.linkedin.com/in/tabitha-example",
                "Tabitha Afzal",
                "Communications professional at Example International Organization",
            ),
            (
                "https://example.org/team/tabitha-afzal",
                "Tabitha Afzal Imdad — Example International Organization",
                "Communications officer, Cape Town",
            ),
            (
                "https://example.net/blog/tabitha-afzal-the-cyclist",
                "Tabitha Afzal wins the regional road race",
                "Cycling club news",
            ),
        ],
        "Tabitha Afzal Imdad": [
            (
                "https://example.com/publications/imdad-2024.pdf",
                "Tabitha Afzal Imdad (2024) — Example International Organization report",
                "Annual communications review",
            ),
        ],
    },
    known_urls=[
        "https://www.linkedin.com/in/tabitha-example",
        "https://example.org/team/tabitha-afzal",
        "https://example.com/publications/imdad-2024.pdf",
    ],
    decoy_urls=["https://example.net/blog/tabitha-afzal-the-cyclist"],
    pages={
        "https://example.org/team/tabitha-afzal": (
            "<html><head><title>Tabitha Afzal Imdad</title></head><body>"
            "<p>Tabitha Afzal Imdad is a communications officer at Example "
            "International Organization in Cape Town.</p></body></html>"
        ),
        "https://example.net/blog/tabitha-afzal-the-cyclist": (
            "<html><head><title>Tabitha Afzal wins the road race</title></head><body>"
            "<p>Our club rider Tabitha Afzal took the regional title on Sunday.</p>"
            "</body></html>"
        ),
    },
)

ASNATH = BenchmarkCase(
    label="Asnath Samar (synthetic)",
    canonical="Asnath Samar",
    context={"organizations": ["Example University"], "places": ["Example City"]},
    answers={
        "Asnath Samar": [
            (
                "https://example.org/staff/asnath-samar",
                "Asnath Samar — Example University",
                "Lecturer, Department of Examples, Example City",
            ),
            (
                "https://example.com/research/samar-2023",
                "Asnath Samar (2023) — Example University",
                "Working paper",
            ),
            (
                "https://example.net/directory/a-samar",
                "A. Samar — unrelated directory listing",
                "Directory entry",
            ),
        ],
    },
    known_urls=[
        "https://example.org/staff/asnath-samar",
        "https://example.com/research/samar-2023",
    ],
    decoy_urls=["https://example.net/directory/a-samar"],
    pages={
        "https://example.org/staff/asnath-samar": (
            "<html><head><title>Asnath Samar</title></head><body>"
            "<p>Asnath Samar is a lecturer at Example University, Example City.</p>"
            "</body></html>"
        ),
    },
)

CASES = [TABITHA, ASNATH]


# ------------------------------------------------------------- the providers


class DeterministicProvider(SearchProvider):
    """A provider that runs exactly the query it is handed and describes results."""

    key = "benchmark_deterministic"
    display_name = "Deterministic benchmark provider"

    def __init__(self, case: BenchmarkCase):
        self.case = case

    def is_available(self):
        return True, ""

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        # Longest needle first: "Tabitha Afzal" is a substring of "Tabitha Afzal
        # Imdad", and matching the shorter one first would make the canonical
        # spelling unreachable — a fixture artefact that would read as a recall
        # failure in the product.
        for needle in sorted(self.case.answers, key=len, reverse=True):
            rows = self.case.answers[needle]
            if needle.lower() in query.lower():
                return [
                    SearchResult(
                        title=title,
                        url=url,
                        provider=self.key,
                        snippet=snippet,
                        provider_position=index,
                        position_is_rank=True,
                        query=query,
                    )
                    for index, (url, title, snippet) in enumerate(rows[:limit], start=1)
                ]
        return []


def _anthropic_payload(case: BenchmarkCase) -> dict:
    """One response carrying every row the case knows, with no descriptions.

    Shaped exactly as Anthropic documents: url, title, page_age, and an opaque
    blob. No snippet field exists, which is the whole point of measuring this
    provider separately.
    """
    content: list[dict] = []
    for index, (needle, rows) in enumerate(case.answers.items(), start=1):
        call_id = f"srvtoolu_{index:02d}"
        content.append(
            {
                "type": "server_tool_use",
                "id": call_id,
                "name": "web_search",
                "input": {"query": needle.lower()},
            }
        )
        content.append(
            {
                "type": "web_search_tool_result",
                "tool_use_id": call_id,
                "content": [
                    {
                        "type": "web_search_result",
                        "url": url,
                        "title": title,
                        "page_age": "April 30, 2025",
                        "encrypted_content": "OPAQUE",
                    }
                    for url, title, _snippet in rows
                ],
            }
        )
    content.append({"type": "text", "text": "DONE"})
    return {
        "role": "assistant",
        "content": content,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 5000,
            "output_tokens": 100,
            "server_tool_use": {"web_search_requests": len(case.answers)},
        },
    }


# ------------------------------------------------------------------ harness


def _case_with_person(session, case: BenchmarkCase):
    row = Case(name=f"Benchmark: {case.label}")
    session.add(row)
    session.flush()
    target = Target(
        case_id=row.id,
        type=TargetType.PERSON,
        raw_input=case.canonical,
        normalized_value=case.canonical.lower(),
        attributes={"display_name": case.canonical, "context": case.context},
    )
    session.add(target)
    session.flush()
    return row, target


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "environment": "test", "result_enrichment_enabled": False}
    base.update(overrides)
    return Settings(**base)


def _mock_pages(case: BenchmarkCase) -> None:
    """robots.txt and the public pages enrichment is permitted to read."""
    for host in ("example.org", "example.net", "example.com"):
        respx.get(f"https://{host}/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
        )
    for url, html in case.pages.items():
        respx.get(url).mock(return_value=httpx.Response(200, html=html))
    # Any other page the ranking might reach: reachable, and says nothing.
    respx.get(url__regex=r"https://example\.(org|net|com)/.*").mock(
        return_value=httpx.Response(200, html="<html><body><p>No public detail.</p></body></html>")
    )


async def _run(session, case: BenchmarkCase, provider, settings) -> BenchmarkMetrics:
    row, target = _case_with_person(session, case)
    report = await search_target(
        session,
        case_id=row.id,
        target_id=target.id,
        provider=provider,
        settings=settings,
    )
    session.flush()
    return measure(
        session,
        case_id=row.id,
        report=report,
        known_urls=case.known_urls,
        decoy_urls=case.decoy_urls,
        label=f"{case.label} · {report.provider}",
    )


# -------------------------------------------------------------------- tests


@pytest.mark.parametrize("case", CASES, ids=[item.label for item in CASES])
async def test_a_deterministic_provider_finds_the_known_pages_and_no_false_matches(
    db_session, case
):
    metrics = await _run(db_session, case, DeterministicProvider(case), _settings())
    print("\n" + render([metrics]))

    # Recall: every known page was stored. A provider that describes its results
    # needs no page reads to get there.
    assert metrics.known_url_recall == 1.0, metrics.missed_urls
    assert metrics.enrichment_fetches == 0
    # Usefulness: the descriptions carry the supplied anchors, so the results are
    # leads rather than bare URLs.
    assert metrics.useful_results >= len(case.known_urls) - 1
    # And the two invariants that must never move.
    assert metrics.false_associations == 0, metrics.false_association_urls
    assert metrics.auto_confirmations == 0
    assert metrics.clean is True


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=[item.label for item in CASES])
async def test_the_anthropic_channel_finds_the_known_pages_with_a_capped_page_read(
    db_session, mock_http, case
):
    """Recall holds; context costs a capped number of page reads.

    This is the measured version of the channel's defining weakness. The provider
    returns no descriptions, so without reading a page the only link between a
    result and the subject is the name it displays — and the scores stay below
    POSSIBLE_MATCH accordingly. Reading the one highest-value page recovers the
    anchor, for one HTTP request rather than a synthesised snippet.
    """
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_anthropic_payload(case)))
    _mock_pages(case)

    from app.services.providers.anthropic_web_search import AnthropicWebSearchProvider

    settings = _settings(
        search_provider="anthropic_web_search",
        anthropic_api_key=KEY,
        anthropic_web_search_max_uses=4,
        result_enrichment_enabled=True,
        max_result_enrichments_per_investigation=3,
    )
    metrics = await _run(db_session, case, AnthropicWebSearchProvider(settings), settings)
    print("\n" + render([metrics]))

    assert metrics.known_url_recall == 1.0, metrics.missed_urls
    # The budget is a ceiling, and it was respected.
    assert metrics.searches_used <= (metrics.search_budget or metrics.searches_used)
    assert metrics.enrichment_fetches <= metrics.enrichment_limit == 3
    # Every stored result came with no description, and the count says so.
    assert metrics.low_context_results == metrics.results_stored
    # The invariants again, on the channel that is most at risk of breaking them.
    assert metrics.false_associations == 0, metrics.false_association_urls
    assert metrics.auto_confirmations == 0
    assert metrics.clean is True


@respx.mock
async def test_without_a_page_read_an_anthropic_result_stays_below_possible_match(
    db_session, mock_http
):
    """The floor the missing snippet imposes, asserted rather than assumed."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_anthropic_payload(TABITHA)))

    from app.services.providers.anthropic_web_search import AnthropicWebSearchProvider

    settings = _settings(
        search_provider="anthropic_web_search",
        anthropic_api_key=KEY,
        result_enrichment_enabled=False,
    )
    metrics = await _run(db_session, TABITHA, AnthropicWebSearchProvider(settings), settings)
    assert metrics.enrichment_fetches == 0
    assert metrics.name_only_above_possible == 0
    assert metrics.useful_results < metrics.results_stored


@respx.mock
async def test_a_decoy_page_that_carries_no_anchor_is_not_raised_by_reading_it(
    db_session, mock_http
):
    """Enrichment must not turn a same-name stranger into a probable match.

    The decoy page really does carry the name — that is what makes it a decoy. It
    does not carry the supplied employer or place, so reading it adds provenance
    and no confidence, which is exactly the intended behaviour.
    """
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_anthropic_payload(TABITHA)))
    _mock_pages(TABITHA)

    from app.services.providers.anthropic_web_search import AnthropicWebSearchProvider

    settings = _settings(
        search_provider="anthropic_web_search",
        anthropic_api_key=KEY,
        result_enrichment_enabled=True,
        max_result_enrichments_per_investigation=5,
    )
    row, target = _case_with_person(db_session, TABITHA)
    report = await search_target(
        db_session,
        case_id=row.id,
        target_id=target.id,
        provider=AnthropicWebSearchProvider(settings),
        settings=settings,
    )
    db_session.flush()
    from sqlalchemy import select

    from app.models import Finding

    findings = {
        str((item.data or {}).get("url")): item
        for item in db_session.scalars(select(Finding).where(Finding.case_id == row.id))
    }
    decoy = findings["https://example.net/blog/tabitha-afzal-the-cyclist"]
    assert decoy.data["affiliations"] == []
    assert decoy.confidence < POSSIBLE_MATCH_THRESHOLD
    assert report.enrichment["fetches_used"] >= 1


async def test_the_benchmark_refuses_to_report_recall_without_a_ground_truth(db_session):
    """A recall figure with no ground truth is a number with no meaning."""
    metrics = measure(
        db_session,
        case_id=_case_with_person(db_session, TABITHA)[0].id,
        report=type("R", (), {"provider": "none", "results_stored": 0})(),
        known_urls=[],
        decoy_urls=[],
        label="empty",
    )
    assert metrics.known_urls == 0
    assert metrics.known_url_recall == 0.0
    assert metrics.useful_result_recall == 0.0
