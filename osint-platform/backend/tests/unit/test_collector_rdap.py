"""RDAP collector: parsing, privacy handling and HTTP behaviour."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.rdap import RDAPCollector
from app.core.errors import CollectorError
from app.models.enums import FindingKind, TargetType
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return RDAPCollector()


def test_parses_registration_dates_and_registrar(collector, fixture):
    document = fixture("rdap_example_com.json")
    raw = RawPayload(source_url="https://rdap.org/domain/example.com", content=document)
    finding = collector.normalize(raw, normalize_target("example.com"))[0]

    assert finding.kind is FindingKind.DOMAIN_REGISTRATION
    assert finding.data["created_at"].startswith("1995-08-14")
    assert finding.data["expires_at"].startswith("2026-08-13")
    assert finding.data["updated_at"].startswith("2025-08-14")
    assert finding.data["registrar"] == "RESERVED-Internet Assigned Numbers Authority"
    assert finding.observed_at is not None
    assert finding.observed_at.year == 1995


def test_nameservers_are_lowercased_and_sorted(collector, fixture):
    raw = RawPayload(source_url="x", content=fixture("rdap_example_com.json"))
    finding = collector.normalize(raw, normalize_target("example.com"))[0]
    assert finding.data["nameservers"] == ["a.iana-servers.net", "b.iana-servers.net"]


def test_redacted_registrant_is_flagged_not_retrieved(collector, fixture):
    raw = RawPayload(source_url="x", content=fixture("rdap_example_com.json"))
    finding = collector.normalize(raw, normalize_target("example.com"))[0]

    assert finding.data["privacy_protected"] is True
    assert "registrant_organization" not in finding.data
    assert any("redacted" in reason.lower() for reason in finding.confidence_reasons)


def test_public_registrant_organization_is_kept(collector, fixture):
    raw = RawPayload(source_url="x", content=fixture("rdap_example_org.json"))
    finding = collector.normalize(raw, normalize_target("example.org"))[0]

    assert finding.data["privacy_protected"] is False
    assert finding.data["registrant_organization"] == "Example Documentation Trust"
    assert finding.data["registrar"] == "Example Registrar LLC"


def test_notable_statuses_are_surfaced(collector, fixture):
    raw = RawPayload(source_url="x", content=fixture("rdap_example_com.json"))
    finding = collector.normalize(raw, normalize_target("example.com"))[0]
    assert "client transfer prohibited" in finding.data["notable_statuses"]


def test_non_dict_document_yields_nothing(collector):
    raw = RawPayload(source_url="x", content=["not", "a", "document"])
    assert collector.normalize(raw, normalize_target("example.com")) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", ("domain", "example.com")),
        ("sub.example.co.uk", ("domain", "example.co.uk")),
        ("https://shop.example.com/a", ("domain", "example.com")),
        ("user@example.org", ("domain", "example.org")),
        ("203.0.113.7", ("ip", "203.0.113.7")),
    ],
)
def test_query_value_derivation(collector, raw, expected):
    assert collector._query_value(normalize_target(raw)) == expected


@respx.mock
async def test_collect_fetches_and_normalises(collector, collector_ctx, fixture, mock_http):
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, json=fixture("rdap_example_com.json"))
    )
    result = await collector.collect(normalize_target("example.com"), collector_ctx)

    assert len(result.findings) == 1
    assert len(result.payloads) == 1
    assert result.payloads[0].source_url.endswith("/domain/example.com")
    assert result.stats["status"] == 200


@respx.mock
async def test_missing_registration_is_a_note_not_a_failure(collector, collector_ctx, mock_http):
    respx.get("https://rdap.org/domain/example.com").mock(return_value=httpx.Response(404))
    result = await collector.collect(normalize_target("example.com"), collector_ctx)
    assert result.findings == []
    assert result.notes


@respx.mock
async def test_server_error_raises(collector, collector_ctx, mock_http):
    respx.get("https://rdap.org/domain/example.com").mock(return_value=httpx.Response(400))
    with pytest.raises(CollectorError, match="HTTP 400"):
        await collector.collect(normalize_target("example.com"), collector_ctx)


@respx.mock
async def test_invalid_json_raises(collector, collector_ctx, mock_http):
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, text="<html>not rdap</html>")
    )
    with pytest.raises(CollectorError, match="valid JSON"):
        await collector.collect(normalize_target("example.com"), collector_ctx)


def test_supported_targets():
    assert RDAPCollector.accepts(TargetType.DOMAIN)
    assert RDAPCollector.accepts(TargetType.IP)
    assert not RDAPCollector.accepts(TargetType.USERNAME)


def test_redaction_markers(collector):
    from app.collectors.rdap import _looks_redacted

    assert _looks_redacted("REDACTED FOR PRIVACY")
    assert _looks_redacted("Domains By Proxy, LLC")
    assert not _looks_redacted("Example Documentation Trust")
