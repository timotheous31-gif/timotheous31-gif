"""The shared HTTP client's lifecycle, under the loops the product actually uses.

Three loop shapes occur in this system and all three are exercised here:

* one ``asyncio.run`` per target, sequentially — the engine's collection stage;
* many collectors concurrently inside one of those loops — ``run_all``;
* the API's own loop running at the same time as an inline job's loop in a
  worker thread, which is what ``POST /cases/{id}/run`` does whenever no Celery
  worker is consuming the queue.

The failure these guard against is not hypothetical: an ``httpx.AsyncClient``
and an ``asyncio.Semaphore`` both bind themselves to one loop and then refuse
another, so module-level state shared across loops produced "Event loop is
closed" on Crossref and "is bound to a different event loop" under contention.
"""

from __future__ import annotations

import asyncio
import threading
import uuid as _uuid

import httpx
import pytest
import respx

from app.collectors.base import CollectorContext
from app.collectors.crossref import CrossrefCollector
from app.collectors.github_people import GitHubPeopleCollector
from app.collectors.openalex import OpenAlexCollector
from app.collectors.orcid import OrcidCollector
from app.collectors.reddit import RedditCollector
from app.collectors.wikidata import WikidataCollector
from app.core import http
from app.core.ratelimit import ProviderLimiter, RateLimit
from app.models.enums import TargetType
from app.services.normalization import normalize_target

NAME = "Tabitha Afzal Imdad"

#: Empty-but-valid bodies. This file is about lifecycle, not parsing, and an
#: empty result set is the shape every one of these APIs can legitimately return.
BODIES: dict[str, dict] = {
    "https://api.crossref.org": {"message": {"items": [], "total-results": 0}},
    "https://api.openalex.org": {"results": [], "meta": {"count": 0}},
    "https://pub.orcid.org": {"num-found": 0, "expanded-result": []},
    "https://www.wikidata.org": {"search": []},
    "https://api.github.com": {"total_count": 0, "items": []},
    "https://www.reddit.com": {"data": {"children": []}},
}

COLLECTORS = [
    CrossrefCollector,
    OpenAlexCollector,
    OrcidCollector,
    WikidataCollector,
    GitHubPeopleCollector,
    RedditCollector,
]


def _routes(router: respx.Router) -> None:
    for prefix, body in BODIES.items():
        router.route(url__startswith=prefix).mock(return_value=httpx.Response(200, json=body))


def _target():
    return normalize_target(NAME, TargetType.PERSON)


def _no_loop_errors(result) -> None:
    """No note, error or reason may mention a closed or mismatched loop."""
    text = " ".join(
        [
            *[str(note) for note in result.notes],
            *[str(error) for error in getattr(result, "errors", []) or []],
            str(getattr(result, "error", "") or ""),
        ]
    ).lower()
    assert "event loop is closed" not in text, text
    assert "bound to a different event loop" not in text, text


@pytest.fixture(autouse=True)
def _own_the_client():
    """No injected client: these tests are about the one the module builds."""
    http.set_http_client(None)
    yield
    http.set_http_client(None)
    http._clients.clear()


def test_every_free_collector_survives_its_own_sequence_of_event_loops():
    """Sequential ``asyncio.run`` per collector, three rounds, six sources."""
    with respx.mock(assert_all_called=False) as router:
        _routes(router)
        for collector_type in COLLECTORS:
            for _ in range(3):

                async def once(collector_type=collector_type):
                    try:
                        return await collector_type().collect(
                            _target(), CollectorContext(case_id=_uuid.uuid4())
                        )
                    finally:
                        await http.close_owned_client()

                _no_loop_errors(asyncio.run(once()))
        assert not http._clients, "every loop closed its own pool"


def test_all_six_collectors_run_concurrently_and_then_again_in_a_fresh_loop():
    """Concurrency inside a loop, repeated across loops.

    Concurrency is the half that matters for the limiter: its lock and semaphore
    only bind to a loop once something actually waits on them, so an uncontended
    first round would have hidden the defect this asserts is gone.
    """
    with respx.mock(assert_all_called=False) as router:
        _routes(router)

        async def round_trip():
            try:
                return await asyncio.gather(
                    *[
                        collector_type().collect(_target(), CollectorContext(case_id=_uuid.uuid4()))
                        for collector_type in COLLECTORS
                        # Twice each, so the per-provider concurrency cap is met.
                        for _ in range(2)
                    ]
                )
            finally:
                await http.close_owned_client()

        for _ in range(3):
            for result in asyncio.run(round_trip()):
                _no_loop_errors(result)
        assert not http._clients


def test_a_contended_limiter_is_reusable_in_a_new_event_loop():
    """The limiter's own loop-bound primitives, isolated from HTTP entirely."""
    limiter = ProviderLimiter()
    limiter.register("crossref", RateLimit(requests=2, per_seconds=1.0, concurrency=2))

    async def hammer():
        async def one():
            async with limiter.slot("crossref"):
                await limiter.acquire("crossref")

        # Eight against a cap of two: the semaphore and the bucket's lock both
        # have to suspend, which is what binds them to this loop.
        await asyncio.gather(*[one() for _ in range(8)])

    asyncio.run(hammer())
    asyncio.run(hammer())  # RuntimeError before the fix.


def test_a_budget_already_spent_is_not_refilled_by_a_new_loop():
    """Rebuilding the lock must not hand a new loop a full bucket."""
    from app.core.ratelimit import AsyncTokenBucket

    bucket = AsyncTokenBucket(rate_per_second=2.0, capacity=2.0)

    async def spend():
        await bucket.acquire()

    asyncio.run(spend())
    spent = bucket.tokens
    asyncio.run(spend())
    assert bucket.tokens < spent + 1.0, "the token budget must survive the loop change"


def test_two_live_loops_keep_separate_pools_and_close_only_their_own():
    """The API's loop and an inline job's loop, overlapping on purpose.

    ``POST /cases/{id}/run`` is a sync endpoint, so FastAPI runs it in a worker
    thread, and it executes the job inline when no Celery worker answers. That
    thread's ``asyncio.run`` therefore overlaps the API's own loop. A single
    shared client would be taken from one loop by the other on every request;
    worse, the job's teardown would close the pool the API loop was still using.
    """
    seen: dict[str, httpx.AsyncClient] = {}
    opened = threading.Event()
    works = "https://api.crossref.org/works"

    with respx.mock(assert_all_called=False) as router:
        _routes(router)

        async def job_loop():
            opened.wait(5)
            seen["job"] = await http.get_http_client()
            await http.get(works, provider="crossref")
            await http.close_owned_client()
            assert seen["job"].is_closed

        thread = threading.Thread(target=lambda: asyncio.run(job_loop()))

        async def api_loop():
            seen["api"] = await http.get_http_client()
            opened.set()
            # Hold this loop's pool open across the whole of the job's lifetime,
            # teardown included.
            await asyncio.to_thread(thread.join, 10)
            assert not seen["api"].is_closed, "another loop closed the API's pool"
            await http.get(works, provider="crossref")
            await http.close_owned_client()

        thread.start()
        try:
            asyncio.run(api_loop())
        finally:
            thread.join(10)

    assert seen["api"] is not seen["job"], "each loop must own its own pool"
    assert seen["api"].is_closed and seen["job"].is_closed
    assert not http._clients, "no pool may be left registered"
