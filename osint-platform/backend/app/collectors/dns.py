"""DNS collector.

Resolves the record types that describe how a domain is published: A, AAAA, MX,
TXT, NS, CNAME and SOA. All of this is public infrastructure data — no zone
transfers, no brute-force subdomain enumeration, no queries against private
resolvers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from app.collectors.base import (
    BaseCollector,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import register_collector
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

RECORD_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA")

#: TXT records that describe published policy rather than arbitrary content.
POLICY_PREFIXES = ("v=spf1", "v=DMARC1", "v=DKIM1", "google-site-verification=", "MS=")


@register_collector
class DNSCollector(BaseCollector):
    """Resolve a domain's public DNS records."""

    name = "dns"
    version = "1.0.0"
    description = "Resolves public DNS records (A, AAAA, MX, TXT, NS, CNAME, SOA)."
    supported_targets = [TargetType.DOMAIN, TargetType.URL, TargetType.EMAIL]
    requires_api_key = False
    rate_limit = RateLimit(requests=20, per_seconds=1.0, concurrency=7)
    timeout = 10.0
    default_confidence = 0.95
    source_attribution = "Public DNS (system resolvers)"

    def _hostname(self, target: NormalizedTarget) -> str:
        """The hostname to query for this target type."""
        if target.type is TargetType.DOMAIN:
            return target.value
        if target.type is TargetType.EMAIL:
            return str(target.attributes.get("domain", target.value.rpartition("@")[2]))
        return str(target.attributes.get("host", ""))

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        """Query every record type concurrently; a missing type is not an error."""
        hostname = self._hostname(target)
        if not hostname:
            raise CollectorError(f"Cannot derive a hostname from {target.value!r}")

        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = self.timeout
        resolver.timeout = min(self.timeout, 5.0)

        result = CollectorResult(stats={"hostname": hostname, "queries": len(RECORD_TYPES)})
        answers = await asyncio.gather(
            *(self._query(resolver, hostname, rtype) for rtype in RECORD_TYPES),
            return_exceptions=True,
        )

        found = 0
        for rtype, answer in zip(RECORD_TYPES, answers, strict=True):
            if isinstance(answer, BaseException):
                # Every genuine failure is surfaced as a note, never hidden.
                result.notes.append(f"{rtype}: {type(answer).__name__}")
                continue
            records, ttl = answer
            if not records:
                continue
            found += 1
            payload = RawPayload(
                source_url=f"dns://{hostname}/{rtype}",
                content={"hostname": hostname, "type": rtype, "records": records, "ttl": ttl},
                content_type="application/json",
                retrieved_at=datetime.now(UTC),
            )
            for draft in self.normalize(payload, target):
                result.add(draft, payload)

        result.stats["record_types_found"] = found
        if found == 0:
            result.notes.append(f"No DNS records resolved for {hostname}")
        return result

    async def _query(
        self, resolver: dns.asyncresolver.Resolver, hostname: str, rtype: str
    ) -> tuple[list[str], int | None]:
        """Resolve one record type. Absence returns an empty list, not an error."""
        try:
            answer = await resolver.resolve(hostname, rtype, raise_on_no_answer=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return [], None
        except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
            raise CollectorError(f"DNS {rtype} lookup for {hostname} failed: {exc}") from exc
        if answer.rrset is None:
            return [], None
        return sorted(record.to_text().strip('"') for record in answer.rrset), answer.rrset.ttl

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        """One finding per record type, with the records as structured data."""
        content: dict[str, Any] = raw.content
        rtype = content["type"]
        hostname = content["hostname"]
        records: list[str] = content["records"]
        if not records:
            return []

        data: dict[str, Any] = {
            "hostname": hostname,
            "record_type": rtype,
            "records": records,
            "ttl": content.get("ttl"),
        }
        summary = f"{len(records)} {rtype} record(s) for {hostname}"

        if rtype == "MX":
            # A null MX ("0 .") is a published statement that the domain
            # accepts no mail. It is a fact worth recording, but it names no
            # host, so it must not become an empty hostname downstream.
            hosts = {part.split()[-1].rstrip(".") for part in records if part.split()}
            data["mail_hosts"] = sorted(host for host in hosts if host)
            data["null_mx"] = "" in hosts and not data["mail_hosts"]
        elif rtype == "NS":
            data["nameservers"] = sorted(
                {host for host in (record.rstrip(".") for record in records) if host}
            )
        elif rtype == "TXT":
            data["policies"] = sorted(
                {
                    record.split("=")[0] + "=" + record.split("=")[1].split()[0]
                    for record in records
                    if record.startswith(POLICY_PREFIXES[:2])
                }
            )
            data["has_spf"] = any(record.lower().startswith("v=spf1") for record in records)
            data["has_dmarc"] = any(record.lower().startswith("v=dmarc1") for record in records)
        elif rtype == "SOA" and records:
            parts = records[0].split()
            if len(parts) >= 2:
                data["primary_nameserver"] = parts[0].rstrip(".")
                # The SOA RNAME is a role mailbox published in the zone itself.
                data["responsible_party"] = parts[1].rstrip(".")

        return [
            FindingDraft(
                kind=FindingKind.DNS_RECORD,
                title=f"DNS {rtype} for {hostname}",
                summary=summary,
                data=data,
                source_url=raw.source_url,
                confidence=self.default_confidence,
                confidence_reasons=[
                    "Resolved directly from public DNS, which is authoritative for this fact"
                ],
                classification=Classification.PUBLIC,
                dedupe_key=f"dns:{hostname}:{rtype}",
            )
        ]
