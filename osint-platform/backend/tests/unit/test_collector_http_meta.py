"""HTTP metadata collector: parsing, robots compliance and politeness."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.http_meta import HTTPMetadataCollector
from app.core.errors import PolicyError
from app.models.enums import FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return HTTPMetadataCollector()


def raw_payload(html: str, **overrides) -> RawPayload:
    content = {
        "url": "https://example.com/",
        "final_url": "https://example.com/",
        "status_code": 200,
        "headers": {"server": "ExampleServer", "content-type": "text/html"},
        "redirects": [],
        "html": html,
        "elapsed_ms": 42.0,
    }
    content.update(overrides)
    return RawPayload(source_url=content["url"], content=content)


def test_extracts_title_description_and_generator(collector, fixture):
    findings = collector.normalize(
        raw_payload(fixture("homepage.html")), normalize_target("example.com")
    )
    metadata = next(f for f in findings if f.kind is FindingKind.HTTP_METADATA)
    assert metadata.data["title"] == "Example Documentation Domain"
    assert "documentation examples" in metadata.data["description"]
    assert metadata.data["generator"] == "ExampleCMS 3.1"
    assert metadata.data["canonical_url"] == "https://example.com/"
    assert metadata.data["site_name"] == "Example"


def test_records_server_headers_and_https(collector, fixture):
    findings = collector.normalize(
        raw_payload(fixture("homepage.html")), normalize_target("example.com")
    )
    metadata = findings[0]
    assert metadata.data["server_headers"]["server"] == "ExampleServer"
    assert metadata.data["https"] is True


def test_security_header_scoring(collector, fixture):
    findings = collector.normalize(
        raw_payload(
            fixture("homepage.html"),
            headers={
                "strict-transport-security": "max-age=63072000",
                "x-content-type-options": "nosniff",
            },
        ),
        normalize_target("example.com"),
    )
    security = next(f for f in findings if f.kind is FindingKind.SECURITY_HEADER)
    assert set(security.data["present"]) == {
        "strict-transport-security",
        "x-content-type-options",
    }
    assert "content-security-policy" in security.data["missing"]
    assert 0 < security.data["score"] < 1


def test_self_published_profile_links_are_high_confidence(collector, fixture):
    findings = collector.normalize(
        raw_payload(fixture("homepage.html")), normalize_target("example.com")
    )
    links = [f for f in findings if f.data.get("relation") == "site_links_to_profile"]
    targets = sorted(f.data["to"] for f in links)
    assert targets == ["https://github.com/exampleorg", "https://www.reddit.com/u/exampleuser"]
    assert all(link.confidence >= 0.85 for link in links)
    assert all(link.confidence_reasons for link in links)


def test_unrelated_and_relative_links_are_ignored(collector, fixture):
    findings = collector.normalize(
        raw_payload(fixture("homepage.html")), normalize_target("example.com")
    )
    assert not any("unrelated.test" in str(f.data) for f in findings)
    assert not any(f.data.get("to") == "/internal" for f in findings)


def test_redirect_chain_is_recorded(collector, fixture):
    findings = collector.normalize(
        raw_payload(
            fixture("homepage.html"),
            redirects=["https://example.com/", "https://www.example.com/"],
            final_url="https://www.example.com/",
        ),
        normalize_target("example.com"),
    )
    assert findings[0].data["redirect_chain"] == [
        "https://example.com/",
        "https://www.example.com/",
    ]
    assert "2 redirect(s)" in findings[0].summary


def test_non_html_response_still_yields_metadata(collector):
    findings = collector.normalize(raw_payload(""), normalize_target("example.com"))
    assert findings[0].data["status_code"] == 200
    assert "title" not in findings[0].data


@respx.mock
async def test_collect_fetches_page_and_probes_well_known(
    collector, collector_ctx, fixture, mock_http
):
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200, text=fixture("homepage.html"), headers={"content-type": "text/html"}
        )
    )
    respx.head("https://example.com/robots.txt").mock(return_value=httpx.Response(200))
    respx.head("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))

    result = await collector.collect(normalize_target("example.com"), collector_ctx)

    kinds = [f.data.get("file") for f in result.findings if "file" in f.data]
    assert sorted(kinds) == ["robots.txt", "sitemap.xml"]
    robots = next(f for f in result.findings if f.data.get("file") == "robots.txt")
    sitemap = next(f for f in result.findings if f.data.get("file") == "sitemap.xml")
    assert robots.data["exists"] is True
    assert sitemap.data["exists"] is False
    assert result.stats["status"] == 200


@respx.mock
async def test_robots_disallow_is_honoured(collector, collector_ctx, mock_http):
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    page = respx.get("https://example.com/").mock(return_value=httpx.Response(200, text="<html/>"))

    with pytest.raises(PolicyError, match=r"robots\.txt"):
        await collector.collect(normalize_target("example.com"), collector_ctx)
    assert page.call_count == 0


@respx.mock
async def test_missing_robots_allows_collection(collector, collector_ctx, fixture, mock_http):
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200, text=fixture("homepage.html"), headers={"content-type": "text/html"}
        )
    )
    respx.head("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.head("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))

    result = await collector.collect(normalize_target("example.com"), collector_ctx)
    assert result.stats["robots_txt"].startswith("absent")
    assert result.findings


@respx.mock
async def test_robots_can_be_disabled_by_configuration(collector_ctx, mock_http, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("RESPECT_ROBOTS_TXT", "false")
    reset_settings_cache()
    try:
        collector = HTTPMetadataCollector()
        robots = respx.get("https://example.com/robots.txt")
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200, text="<html><title>x</title></html>", headers={"content-type": "text/html"}
            )
        )
        respx.head("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
        respx.head("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))

        collector_ctx.settings = collector.settings
        result = await collector.collect(normalize_target("example.com"), collector_ctx)
        assert robots.call_count == 0
        assert result.findings
    finally:
        reset_settings_cache()


def test_url_targets_use_their_own_path(collector):
    assert collector._url(normalize_target("https://example.com/about")) == (
        "https://example.com/about"
    )
    assert collector._url(normalize_target("example.com")) == "https://example.com/"
