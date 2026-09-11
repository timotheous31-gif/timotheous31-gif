"""Source coverage, execution accounting, and the Crossref event loop.

The worst thing a report can do is let "nobody looked" read as "there is nothing
to find". The first half of this file is about keeping those apart. The second is
about a real intermittent failure — ``RuntimeError: Event loop is closed`` from
Crossref on a rerun — and the invariant that prevents it.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid

import httpx
import pytest
import respx

from app.models.enums import RunStatus
from app.reporting.coverage import (
    ABSENCE_STATES,
    GAP_STATES,
    STATE_MEANINGS,
    CoverageState,
    image_search_coverage,
    state_for_run,
    summarise_gaps,
    web_search_coverage,
)

NAME = "Tabitha Afzal Imdad"


# --------------------------------------------------------- the state machine


def test_only_a_genuine_empty_answer_supports_absence():
    """One state, and one only, may be read as evidence of absence."""
    assert {CoverageState.NO_MATCH_RETURNED} == ABSENCE_STATES
    for state in CoverageState:
        if state is not CoverageState.NO_MATCH_RETURNED:
            assert state not in ABSENCE_STATES


def test_every_state_explains_itself():
    for state in CoverageState:
        assert STATE_MEANINGS[state], f"{state} has no meaning for a reader"


def test_a_not_searched_state_says_it_is_not_evidence_of_absence():
    for state in (
        CoverageState.NOT_SEARCHED,
        CoverageState.PROVIDER_NOT_CONFIGURED,
        CoverageState.SKIPPED,
        CoverageState.FAILED,
    ):
        assert "not evidence" in STATE_MEANINGS[state].lower() or (
            "not evidence that nothing exists" in STATE_MEANINGS[state].lower()
        ), state
        assert state in GAP_STATES


@pytest.mark.parametrize(
    "status,findings,expected",
    [
        (RunStatus.SUCCESS, 3, CoverageState.FOUND),
        (RunStatus.SUCCESS, 0, CoverageState.NO_MATCH_RETURNED),
        (RunStatus.PARTIAL, 1, CoverageState.FOUND),
        (RunStatus.SKIPPED, 0, CoverageState.SKIPPED),
        (RunStatus.FAILED, 0, CoverageState.FAILED),
        (RunStatus.TIMEOUT, 0, CoverageState.FAILED),
        (RunStatus.PENDING, 0, CoverageState.NOT_SEARCHED),
        (RunStatus.RUNNING, 0, CoverageState.NOT_SEARCHED),
    ],
)
def test_a_run_status_maps_to_exactly_one_coverage_state(status, findings, expected):
    assert state_for_run(status, findings) is expected


def test_a_failed_source_is_never_reported_as_a_negative_result():
    item = web_search_coverage(
        provider="serper", configured=True, queries_run=0, results_stored=0, failures=2
    )
    assert item.state is CoverageState.FAILED
    assert not item.supports_absence
    assert item.is_gap


def test_an_unconfigured_provider_is_its_own_state():
    item = web_search_coverage(
        provider="none", configured=False, queries_run=0, results_stored=0, reason="not set"
    )
    assert item.state is CoverageState.PROVIDER_NOT_CONFIGURED
    assert not item.supports_absence
    assert "not evidence" in STATE_MEANINGS[item.state].lower()


def test_a_provider_that_searched_and_found_nothing_is_an_absence():
    item = web_search_coverage(provider="serper", configured=True, queries_run=6, results_stored=0)
    assert item.state is CoverageState.NO_MATCH_RETURNED
    assert item.supports_absence


def test_image_coverage_is_manual_when_no_provider_is_configured():
    item = image_search_coverage(images=0, image_queries_run=0)
    assert item.state is CoverageState.MANUAL_REVIEW_AVAILABLE
    assert "never" in item.detail.lower()


def test_image_coverage_is_manual_when_a_provider_ran_no_image_query():
    """A configured provider is not a search.

    This claimed "no public image was returned for the generated image queries"
    whenever a provider was configured — whether or not one image query had been
    issued. An unverified absence, produced by the module that exists to stop
    unverified absences.
    """
    item = image_search_coverage(images=0, image_queries_run=0)
    assert item.state is CoverageState.MANUAL_REVIEW_AVAILABLE
    assert not item.supports_absence

    ran = image_search_coverage(images=0, image_queries_run=3)
    assert ran.state is CoverageState.NO_MATCH_RETURNED
    assert ran.supports_absence
    assert "3 image query" in ran.detail


def test_gap_sentences_name_the_unsearched_channel():
    gaps = summarise_gaps(
        [
            web_search_coverage(provider="none", configured=False, queries_run=0, results_stored=0),
            image_search_coverage(images=0, image_queries_run=0),
        ]
    )
    assert any("public web was not searched" in line for line in gaps)
    assert any("not evidence" in line.lower() for line in gaps)


# ------------------------------------------------------ in a rendered report


async def _case_with_runs(api_client, case_id, rows):
    """Persist collector runs against a job, as an execution would."""
    from app.core.db import get_session_factory
    from app.models import CollectorRun, Job, Target
    from app.models.enums import TargetType

    # Reusable: a second call adds another execution to the same target, which
    # is exactly the rerun this file is about.
    existing = (await api_client.get(f"/api/v1/cases/{case_id}/targets")).json()
    items = existing["items"] if isinstance(existing, dict) else existing
    if items:
        target_id = items[0]["id"]
    else:
        response = await api_client.post(
            f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
        )
        assert response.status_code in (200, 201), response.text
        target_id = response.json()["id"]

    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        assert target.type is TargetType.PERSON
        jobs = []
        for collectors, _label in rows:
            job = Job(case_id=_uuid.UUID(case_id))
            session.add(job)
            session.flush()
            jobs.append(job)
            for collector, status in collectors:
                session.add(
                    CollectorRun(
                        case_id=_uuid.UUID(case_id),
                        target_id=target.id,
                        job_id=job.id,
                        collector=collector,
                        collector_version="1.0.0",
                        status=status,
                        error_message="upstream broke" if status is RunStatus.FAILED else None,
                        error_type="CollectorError" if status is RunStatus.FAILED else None,
                    )
                )
        session.commit()
    return target_id, [str(job.id) for job in jobs]


async def test_the_report_distinguishes_not_searched_from_no_match(api_client, case_id):
    await _case_with_runs(
        api_client,
        case_id,
        [([("orcid", RunStatus.SUCCESS), ("reddit", RunStatus.SKIPPED)], "only")],
    )
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    states = {item["source"]: item["state"] for item in report["coverage"]}
    # ORCID ran and returned nothing: an absence, in ORCID only.
    assert states["orcid"] == "NO_MATCH_RETURNED"
    # Reddit declined: not an absence.
    assert states["reddit"] == "SKIPPED"
    # The public web was never searched, and says so.
    assert states["public_web"] == "PROVIDER_NOT_CONFIGURED"
    assert states["linkedin"] == "MANUAL_REVIEW_AVAILABLE"


async def test_the_executive_summary_refuses_to_imply_comprehensive_absence(api_client, case_id):
    """The Tabitha failure, asserted."""
    await _case_with_runs(api_client, case_id, [([("orcid", RunStatus.SUCCESS)], "only")])
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    summary = " ".join(report["executive_summary"]).lower()
    assert "public web was not searched" in summary
    assert "not a statement that no public record exists" in summary


async def test_the_markdown_report_carries_a_coverage_table(api_client, case_id):
    await _case_with_runs(
        api_client, case_id, [([("orcid", RunStatus.SUCCESS), ("crossref", RunStatus.FAILED)], "x")]
    )
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "## Source coverage" in markdown
    assert "PROVIDER_NOT_CONFIGURED" in markdown
    assert "Gaps a reader must weigh" in markdown
    assert "`FAILED`" in markdown


# --------------------------------------------- current versus historical runs


async def test_the_latest_execution_is_told_apart_from_the_history(api_client, case_id):
    """Five reruns of eight collectors is not forty sources."""
    collectors = [
        ("orcid", RunStatus.SUCCESS),
        ("openalex", RunStatus.SUCCESS),
        ("crossref", RunStatus.FAILED),
        ("reddit", RunStatus.SKIPPED),
    ]
    await _case_with_runs(api_client, case_id, [(collectors, f"run {n}") for n in range(5)])
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    execution = report["execution"]
    assert execution["executions"] == 5
    assert execution["historical_collector_runs"] == 20
    assert execution["latest"]["collectors_attempted"] == 4
    assert execution["latest"]["successful"] == 2
    assert execution["latest"]["failed"] == 1
    assert execution["latest"]["skipped"] == 1
    # The audit trail is intact, not overwritten.
    assert report["counts"]["collector_runs"] == 20


async def test_a_rerun_preserves_the_audit_history(api_client, case_id):
    await _case_with_runs(api_client, case_id, [([("orcid", RunStatus.SUCCESS)], "first")])
    first = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    assert first["execution"]["historical_collector_runs"] == 1

    await _case_with_runs(api_client, case_id, [([("orcid", RunStatus.FAILED)], "second")])
    second = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    assert second["execution"]["historical_collector_runs"] == 2
    # The latest execution describes the latest execution, not the best one.
    assert second["execution"]["latest"]["failed"] == 1
    assert second["execution"]["latest"]["successful"] == 0


async def test_coverage_describes_the_latest_execution_not_the_best_one(api_client, case_id):
    await _case_with_runs(
        api_client,
        case_id,
        [([("orcid", RunStatus.SUCCESS)], "first"), ([("orcid", RunStatus.SKIPPED)], "second")],
    )
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    states = {item["source"]: item["state"] for item in report["coverage"]}
    assert states["orcid"] == "SKIPPED", "today's outcome, not three reruns ago"


# ---------------------------------------------- the Crossref event-loop bug


def test_the_shared_client_is_never_reused_across_event_loops():
    """The root cause, asserted directly.

    ``httpx.AsyncClient`` binds its pool to the loop that created it and
    ``is_closed`` only tracks an explicit ``aclose()``. The engine runs each
    target under its own ``asyncio.run``, so a client that survived the first
    loop handed the second a keep-alive connection belonging to a closed loop —
    and the next request on it raised ``RuntimeError: Event loop is closed``.
    """
    from app.core import http

    http.set_http_client(None)

    async def grab() -> httpx.AsyncClient:
        try:
            return await http.get_http_client()
        finally:
            await http.close_owned_client()

    # The objects are held, not their ids: CPython reuses an address once the
    # first client is freed, which made an id() comparison pass or fail depending
    # on what else the suite had allocated.
    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second, "a client must not outlive the loop that created it"
    assert first.is_closed, "the first client must have been closed in its own loop"


def test_a_forgotten_teardown_still_cannot_resurrect_the_bug():
    """The defensive half: a caller that forgets to close gets a fresh client."""
    from app.core import http

    http.set_http_client(None)

    async def grab() -> httpx.AsyncClient:
        return await http.get_http_client()

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second, "a loop change must force a rebuild even without teardown"
    asyncio.run(http.close_http_client())


def test_an_injected_test_client_is_never_swapped_out(mock_http):
    """Rebuilding a respx-mocked client would silently disconnect the mock."""
    from app.core import http

    async def grab() -> httpx.AsyncClient:
        return await http.get_http_client()

    assert asyncio.run(grab()) is asyncio.run(grab())


def test_close_owned_client_leaves_an_injected_client_alone(mock_http):
    from app.core import http

    async def check() -> bool:
        client = await http.get_http_client()
        await http.close_owned_client()
        return client.is_closed

    assert asyncio.run(check()) is False


@respx.mock
async def test_crossref_survives_repeated_runs(mock_http):
    """A rerun must produce a real outcome, not a closed-loop error."""
    from app.collectors.base import CollectorContext
    from app.collectors.crossref import CrossrefCollector
    from app.models.enums import TargetType
    from app.services.normalization import normalize_target

    respx.get(url__startswith="https://api.crossref.org").mock(
        return_value=httpx.Response(200, json={"message": {"items": [], "total-results": 0}})
    )
    target = normalize_target(NAME, TargetType.PERSON)

    for _ in range(3):
        result = await CrossrefCollector().collect(target, CollectorContext(case_id=_uuid.uuid4()))
        assert not any(
            "event loop is closed" in str(note).lower() for note in result.notes
        ), result.notes


def test_the_engine_closes_the_pool_inside_its_own_loop():
    """The engine owns the loop, so the engine owns the client's lifecycle."""
    import inspect

    from app.services.engine import InvestigationEngine

    source = inspect.getsource(InvestigationEngine._collect)
    assert "close_owned_client" in source
    assert "finally" in source
