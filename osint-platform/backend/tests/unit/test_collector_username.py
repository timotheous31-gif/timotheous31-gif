"""Username presence collector: presence, never identity."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.platforms import PLATFORMS
from app.collectors.username import MAX_PRESENCE_CONFIDENCE, UsernameCollector
from app.models.enums import FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return UsernameCollector()


def presence(exists: bool, platform: str = "github", status: int = 200) -> RawPayload:
    return RawPayload(
        source_url="https://example.test/probe",
        content={
            "platform": platform,
            "username": "exampleuser",
            "exists": exists,
            "status_code": status,
        },
    )


def test_hit_becomes_a_presence_finding(collector):
    finding = collector.normalize(presence(True), normalize_target("@exampleuser"))[0]
    assert finding.kind is FindingKind.USERNAME_PRESENCE
    assert finding.data["platform"] == "github"
    assert finding.data["profile_url"] == "https://github.com/exampleuser"
    assert finding.data["exists"] is True


def test_presence_confidence_is_capped(collector):
    for platform in PLATFORMS:
        finding = collector.normalize(
            presence(True, platform.key), normalize_target("@exampleuser")
        )[0]
        assert finding.confidence <= MAX_PRESENCE_CONFIDENCE


def test_presence_finding_states_it_is_not_identity(collector):
    finding = collector.normalize(presence(True), normalize_target("@exampleuser"))[0]
    assert "not evidence of a shared owner" in " ".join(finding.confidence_reasons)
    assert "not who holds it" in finding.summary


def test_absence_produces_no_finding(collector):
    assert collector.normalize(presence(False), normalize_target("@exampleuser")) == []


@pytest.mark.parametrize(
    "raw",
    [
        "@ExampleUser",
        "https://github.com/exampleuser",
        "https://mastodon.social/@exampleuser",
        "https://reddit.com/u/exampleuser",
    ],
)
def test_username_derived_from_handles_and_profile_urls(collector, raw):
    assert collector._username(normalize_target(raw)) == "exampleuser"


@respx.mock
async def test_collect_checks_every_platform(collector, collector_ctx, mock_http):
    for platform in PLATFORMS:
        respx.get(platform.probe_url("exampleuser")).mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    assert result.stats["found"] == len(PLATFORMS)
    assert len(result.findings) == len(PLATFORMS)


@respx.mock
async def test_missing_profiles_are_counted_not_reported(collector, collector_ctx, mock_http):
    for platform in PLATFORMS:
        respx.get(platform.probe_url("nobodyhere")).mock(return_value=httpx.Response(404))
    result = await collector.collect(normalize_target("@nobodyhere"), collector_ctx)
    assert result.findings == []
    assert result.stats["found"] == 0
    assert result.notes


@respx.mock
async def test_inconclusive_status_is_a_note_not_a_hit(collector, collector_ctx, mock_http):
    for index, platform in enumerate(PLATFORMS):
        status = 403 if index == 0 else 404
        respx.get(platform.probe_url("exampleuser")).mock(return_value=httpx.Response(status))
    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    assert result.findings == []
    assert any("CollectorError" in note for note in result.notes)


@respx.mock
async def test_null_body_marker_means_absent(collector, collector_ctx, mock_http):
    for platform in PLATFORMS:
        body = "null" if platform.key == "hackernews" else '{"id": 1}'
        respx.get(platform.probe_url("exampleuser")).mock(
            return_value=httpx.Response(200, text=body)
        )
    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    platforms = {f.data["platform"] for f in result.findings}
    assert "hackernews" not in platforms
    assert "github" in platforms


@respx.mock
async def test_one_platform_failure_does_not_lose_the_rest(collector, collector_ctx, mock_http):
    for index, platform in enumerate(PLATFORMS):
        route = respx.get(platform.probe_url("exampleuser"))
        if index == 0:
            route.mock(side_effect=httpx.ConnectError("down"))
        else:
            route.mock(return_value=httpx.Response(200, json={"id": 1}))
    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    assert len(result.findings) == len(PLATFORMS) - 1
    assert result.notes


def test_platform_list_only_contains_public_publishing_platforms():
    keys = {platform.key for platform in PLATFORMS}
    # Guard against anyone adding a category where mere presence is sensitive.
    forbidden = {"dating", "health", "finance", "religion", "adult"}
    assert not keys & forbidden
    assert all(platform.url_template.startswith("https://") for platform in PLATFORMS)
    assert all("{username}" in platform.url_template for platform in PLATFORMS)
