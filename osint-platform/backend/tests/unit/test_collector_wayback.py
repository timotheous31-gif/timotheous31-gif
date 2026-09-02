"""Internet Archive collector."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.wayback import WaybackCollector, _parse_timestamp
from app.core.errors import CollectorError
from app.models.enums import FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return WaybackCollector()


@pytest.fixture
def payload(fixture):
    return RawPayload(
        source_url="https://web.archive.org/cdx/search/cdx?url=example.com/*",
        content=fixture("wayback_cdx_example_com.json"),
    )


def test_first_snapshot_is_identified(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    first = next(f for f in findings if f.data.get("position") == "first")
    assert first.data["timestamp"] == "19970126045828"
    assert first.observed_at.year == 1997
    assert first.kind is FindingKind.ARCHIVE_SNAPSHOT


def test_first_snapshot_carries_the_creation_caveat(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    first = next(f for f in findings if f.data.get("position") == "first")
    assert any("later than" in reason for reason in first.confidence_reasons)


def test_coverage_summary(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    coverage = next(f for f in findings if "snapshot_count" in f.data)
    assert coverage.data["snapshot_count"] == 5
    assert coverage.data["years_covered"] == [1997, 2003, 2014, 2020, 2024]
    assert coverage.data["distinct_urls"] == 4


def test_recent_snapshots_have_archive_urls(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    recent = [f for f in findings if f.data.get("position") == "recent"]
    assert recent
    assert all(f.data["archived_url"].startswith("https://web.archive.org/web/") for f in recent)


def test_header_only_response_yields_nothing(collector):
    raw = RawPayload(source_url="x", content=[["timestamp", "original"]])
    assert collector.normalize(raw, normalize_target("example.com")) == []


def test_unparsable_timestamps_are_dropped(collector):
    raw = RawPayload(
        source_url="x",
        content=[["timestamp", "original"], ["not-a-date", "http://example.com/"]],
    )
    assert collector.normalize(raw, normalize_target("example.com")) == []


@respx.mock
async def test_collect_uses_bounded_query(collector, collector_ctx, fixture, mock_http):
    route = respx.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=fixture("wayback_cdx_example_com.json"))
    )
    result = await collector.collect(normalize_target("example.com"), collector_ctx)
    params = route.calls[0].request.url.params
    assert params["url"] == "example.com/*"
    assert int(params["limit"]) <= 500
    assert params["collapse"] == "timestamp:6"
    assert result.stats["snapshots"] == 5


@respx.mock
async def test_url_target_queries_that_url(collector, collector_ctx, fixture, mock_http):
    route = respx.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=fixture("wayback_cdx_example_com.json"))
    )
    await collector.collect(normalize_target("https://example.com/about"), collector_ctx)
    assert route.calls[0].request.url.params["url"] == "example.com/about"


@respx.mock
async def test_empty_archive_is_a_note(collector, collector_ctx, mock_http):
    respx.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, text="")
    )
    result = await collector.collect(normalize_target("example.com"), collector_ctx)
    assert result.findings == []
    assert result.notes


@respx.mock
async def test_error_status_raises(collector, collector_ctx, mock_http):
    respx.get("https://web.archive.org/cdx/search/cdx").mock(return_value=httpx.Response(500))
    with pytest.raises(CollectorError):
        await collector.collect(normalize_target("example.com"), collector_ctx)


@pytest.mark.parametrize(
    ("value", "year"),
    [("19970126045828", 1997), ("20240615", 2024), ("bad", None), ("", None)],
)
def test_timestamp_parsing(value, year):
    parsed = _parse_timestamp(value)
    assert (parsed.year if parsed else None) == year
