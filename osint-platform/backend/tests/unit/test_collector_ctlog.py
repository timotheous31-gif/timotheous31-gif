"""Certificate Transparency collector."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.ctlog import CertificateTransparencyCollector, _issuer_name
from app.core.errors import CollectorError
from app.models.enums import FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return CertificateTransparencyCollector()


@pytest.fixture
def payload(fixture):
    return RawPayload(
        source_url="https://crt.sh/?q=%25.example.com&output=json",
        content=fixture("crtsh_example_com.json"),
    )


def test_certificates_are_normalised(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    certs = [f for f in findings if f.kind is FindingKind.CERTIFICATE and "common_name" in f.data]
    assert {c.data["common_name"] for c in certs} >= {"www.example.com", "api.example.com"}
    first = next(c for c in certs if c.data["common_name"] == "www.example.com")
    assert first.data["issuer"] == "Let's Encrypt"
    assert first.data["not_before"].startswith("2024-02-01")
    assert first.observed_at is not None


def test_subdomains_are_discovered_and_scoped(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    hosts = {f.data["hostname"] for f in findings if f.kind is FindingKind.SUBDOMAIN}
    assert hosts == {"www.example.com", "api.example.com", "staging.example.com", "*.example.com"}
    # A certificate for an unrelated domain must not leak into the results.
    assert not any("other-domain.test" in host for host in hosts)


def test_wildcard_hosts_are_flagged(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    wildcard = next(f for f in findings if f.data.get("hostname") == "*.example.com")
    assert wildcard.data["wildcard"] is True


def test_subdomain_confidence_notes_the_liveness_caveat(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    subdomain = next(f for f in findings if f.kind is FindingKind.SUBDOMAIN)
    assert subdomain.confidence <= 0.85
    assert any("currently live" in reason for reason in subdomain.confidence_reasons)


def test_issuance_summary_aggregates(collector, payload):
    findings = collector.normalize(payload, normalize_target("example.com"))
    summary = next(f for f in findings if f.data.get("certificate_count"))
    assert summary.data["certificate_count"] == 4
    assert summary.data["issuers"]["Let's Encrypt"] == 2
    assert summary.data["earliest_issuance"].startswith("2022-01-10")


def test_empty_result_yields_nothing(collector):
    raw = RawPayload(source_url="x", content=[])
    assert collector.normalize(raw, normalize_target("example.com")) == []


@respx.mock
async def test_collect_queries_crtsh(collector, collector_ctx, fixture, mock_http):
    route = respx.get("https://crt.sh/").mock(
        return_value=httpx.Response(200, json=fixture("crtsh_example_com.json"))
    )
    result = await collector.collect(normalize_target("sub.example.com"), collector_ctx)
    assert route.called
    assert route.calls[0].request.url.params["q"] == "%.example.com"
    assert result.stats["certificates_seen"] == 4
    assert result.findings


@respx.mock
async def test_empty_body_is_a_note(collector, collector_ctx, mock_http):
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(200, text=""))
    result = await collector.collect(normalize_target("example.com"), collector_ctx)
    assert result.findings == []
    assert result.notes


@respx.mock
async def test_error_status_raises(collector, collector_ctx, mock_http):
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(502))
    with pytest.raises(CollectorError):
        await collector.collect(normalize_target("example.com"), collector_ctx)


@pytest.mark.parametrize(
    ("issuer", "expected"),
    [
        ("C=US, O=Let's Encrypt, CN=R3", "Let's Encrypt"),
        ("CN=Only Common Name", "Only Common Name"),
        ("", ""),
    ],
)
def test_issuer_extraction(issuer, expected):
    assert _issuer_name(issuer) == expected
