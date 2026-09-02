"""DNS collector: normalisation from canned answers, and resolver isolation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.collectors.base import RawPayload
from app.collectors.dns import DNSCollector
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return DNSCollector()


def payload(rtype: str, records: list[str], hostname: str = "example.com") -> RawPayload:
    return RawPayload(
        source_url=f"dns://{hostname}/{rtype}",
        content={"hostname": hostname, "type": rtype, "records": records, "ttl": 300},
    )


def test_a_records_become_one_finding(collector):
    target = normalize_target("example.com")
    findings = collector.normalize(payload("A", ["93.184.215.14"]), target)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind is FindingKind.DNS_RECORD
    assert finding.data["records"] == ["93.184.215.14"]
    assert finding.classification is Classification.PUBLIC
    assert finding.confidence >= 0.9
    assert finding.confidence_reasons
    assert finding.dedupe_key == "dns:example.com:A"


def test_mx_records_expose_mail_hosts(collector):
    target = normalize_target("example.com")
    finding = collector.normalize(
        payload("MX", ["10 mail1.example.com.", "20 mail2.example.com."]), target
    )[0]
    assert finding.data["mail_hosts"] == ["mail1.example.com", "mail2.example.com"]


def test_ns_records_are_stripped_of_trailing_dots(collector):
    target = normalize_target("example.com")
    finding = collector.normalize(payload("NS", ["a.iana-servers.net."]), target)[0]
    assert finding.data["nameservers"] == ["a.iana-servers.net"]


def test_txt_records_flag_spf_and_dmarc(collector):
    target = normalize_target("example.com")
    finding = collector.normalize(
        payload("TXT", ["v=spf1 include:_spf.example.com ~all", "unrelated-token"]), target
    )[0]
    assert finding.data["has_spf"] is True
    assert finding.data["has_dmarc"] is False


def test_soa_exposes_primary_nameserver(collector):
    target = normalize_target("example.com")
    finding = collector.normalize(
        payload("SOA", ["ns.icann.org. noc.dns.icann.org. 2024 7200 3600 1209600 3600"]), target
    )[0]
    assert finding.data["primary_nameserver"] == "ns.icann.org"
    assert finding.data["responsible_party"] == "noc.dns.icann.org"


def test_empty_record_set_produces_no_findings(collector):
    assert collector.normalize(payload("AAAA", []), normalize_target("example.com")) == []


@pytest.mark.parametrize(
    ("raw", "expected_host"),
    [
        ("example.com", "example.com"),
        ("https://sub.example.com/page", "sub.example.com"),
        ("user@example.org", "example.org"),
    ],
)
def test_hostname_derivation(collector, raw, expected_host):
    assert collector._hostname(normalize_target(raw)) == expected_host


async def test_collect_survives_individual_query_failures(collector, collector_ctx, monkeypatch):
    """A resolver error for one record type must not lose the others."""

    async def fake_query(self, resolver, hostname, rtype):
        if rtype == "TXT":
            raise TimeoutError("resolver timed out")
        if rtype == "A":
            return ["93.184.215.14"], 300
        return [], None

    monkeypatch.setattr(DNSCollector, "_query", fake_query)
    result = await collector.collect(normalize_target("example.com"), collector_ctx)

    assert len(result.findings) == 1
    assert result.findings[0].data["record_type"] == "A"
    assert any("TXT" in note for note in result.notes)
    assert result.stats["record_types_found"] == 1


async def test_collect_notes_when_nothing_resolves(collector, collector_ctx, monkeypatch):
    async def empty(self, resolver, hostname, rtype):
        return [], None

    monkeypatch.setattr(DNSCollector, "_query", empty)
    result = await collector.collect(normalize_target("nothing.invalid"), collector_ctx)
    assert result.findings == []
    assert result.notes


async def test_collect_rejects_target_without_hostname(collector, collector_ctx):
    from app.core.errors import CollectorError
    from app.services.normalization import NormalizedTarget

    bogus = NormalizedTarget(TargetType.URL, "x", "x", {})
    with pytest.raises(CollectorError):
        await collector.collect(bogus, collector_ctx)


def test_query_treats_nxdomain_as_absence(collector):
    import dns.resolver

    class _Resolver:
        async def resolve(self, *args, **kwargs):
            raise dns.resolver.NXDOMAIN

    import asyncio

    records, ttl = asyncio.run(collector._query(_Resolver(), "absent.invalid", "A"))
    assert records == []
    assert ttl is None


def test_query_raises_on_resolver_failure(collector):
    import asyncio

    import dns.resolver

    from app.core.errors import CollectorError

    class _Resolver:
        async def resolve(self, *args, **kwargs):
            raise dns.resolver.NoNameservers("all resolvers failed")

    with pytest.raises(CollectorError):
        asyncio.run(collector._query(_Resolver(), "example.com", "A"))


def test_metadata_declares_no_api_key():
    assert DNSCollector.metadata()["requires_api_key"] is False
    assert DNSCollector.accepts(TargetType.DOMAIN)


def test_answer_without_rrset_is_empty(collector):
    import asyncio

    class _Resolver:
        async def resolve(self, *args, **kwargs):
            return SimpleNamespace(rrset=None)

    assert asyncio.run(collector._query(_Resolver(), "example.com", "A")) == ([], None)


def test_null_mx_records_no_empty_host(collector):
    """A null MX ("0 .") says the domain accepts no mail; it names no host."""
    finding = collector.normalize(payload("MX", ["0 ."]), normalize_target("example.com"))[0]
    assert finding.data["mail_hosts"] == []
    assert finding.data["null_mx"] is True


def test_mixed_mx_keeps_only_real_hosts(collector):
    finding = collector.normalize(
        payload("MX", ["0 .", "10 mail.example.com."]), normalize_target("example.com")
    )[0]
    assert finding.data["mail_hosts"] == ["mail.example.com"]
    assert finding.data["null_mx"] is False


def test_root_nameserver_is_not_an_empty_host(collector):
    finding = collector.normalize(payload("NS", ["."]), normalize_target("example.com"))[0]
    assert finding.data["nameservers"] == []
