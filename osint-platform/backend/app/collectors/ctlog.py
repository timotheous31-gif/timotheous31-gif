"""Certificate Transparency collector.

CT logs are append-only public records of every certificate a participating CA
issues, so they are one of the few sources that reveal subdomains without any
probing of the target at all. This collector queries crt.sh's JSON interface
and normalises the certificates and the hostnames they cover.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

from app.collectors.base import (
    BaseCollector,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget, registrable_domain

log = get_logger(__name__)

#: crt.sh can return very large result sets for popular domains.
MAX_CERTIFICATES = 500
MAX_SUBDOMAINS = 300


@register_collector
class CertificateTransparencyCollector(BaseCollector):
    """Discover certificates and subdomains from public CT logs."""

    name = "ctlog"
    version = "1.0.0"
    description = (
        "Certificates and subdomains discovered from public Certificate Transparency logs."
    )
    supported_targets = [TargetType.DOMAIN, TargetType.URL]
    requires_api_key = False
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=1)
    timeout = 45.0
    run_timeout = 90.0
    retry = RetryPolicy(attempts=2, base_delay=2.0, max_delay=20.0)
    default_confidence = 0.9
    source_attribution = "crt.sh (Certificate Transparency logs)"

    def _domain(self, target: NormalizedTarget) -> str:
        if target.type is TargetType.DOMAIN:
            return target.value
        return str(target.attributes.get("host", ""))

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        domain = self._domain(target)
        if not domain:
            raise CollectorError(f"Cannot derive a domain from {target.value!r}")

        base = registrable_domain(domain)
        url = f"{self.settings.crtsh_base_url.rstrip('/')}/"
        response = await http.get(
            url,
            provider=self.name,
            params={"q": f"%.{base}", "output": "json"},
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
            headers={"Accept": "application/json"},
        )

        result = CollectorResult(stats={"domain": base, "status": response.status_code})
        if response.status_code == 404:
            result.notes.append(f"No certificate transparency entries published for {base}")
            return result
        if not response.ok:
            raise CollectorError(
                f"crt.sh returned HTTP {response.status_code} for {base}", detail={"url": url}
            )
        if not response.content.strip():
            result.notes.append(f"No certificate transparency entries published for {base}")
            return result

        try:
            entries = response.json()
        except ValueError as exc:
            raise CollectorError("crt.sh returned a response that was not valid JSON") from exc
        if not isinstance(entries, list):
            raise CollectorError("crt.sh returned an unexpected document shape")

        payload = RawPayload(
            source_url=f"{url}?q=%25.{base}&output=json",
            content=entries[:MAX_CERTIFICATES],
            content_type="application/json",
            status_code=response.status_code,
        )
        result.stats["certificates_seen"] = len(entries)
        for draft in self.normalize(payload, target):
            result.add(draft, payload)
        return result

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        entries: list[dict[str, Any]] = [item for item in raw.content if isinstance(item, dict)]
        if not entries:
            return []

        base = registrable_domain(self._domain(target) or target.value)
        findings: list[FindingDraft] = []
        hostnames: set[str] = set()
        issuers: dict[str, int] = defaultdict(int)
        earliest: datetime | None = None

        for entry in entries[:MAX_CERTIFICATES]:
            names = _hostnames(entry, base)
            hostnames.update(names)
            issuer = _issuer_name(entry.get("issuer_name", ""))
            if issuer:
                issuers[issuer] += 1
            not_before = _parse(entry.get("not_before"))
            if not_before and (earliest is None or not_before < earliest):
                earliest = not_before

        for entry in entries[:50]:
            common_name = str(entry.get("common_name") or "").lower().strip()
            not_before = _parse(entry.get("not_before"))
            not_after = _parse(entry.get("not_after"))
            if not common_name:
                continue
            findings.append(
                FindingDraft(
                    kind=FindingKind.CERTIFICATE,
                    title=f"Certificate for {common_name}",
                    summary=(
                        f"Issued by {_issuer_name(entry.get('issuer_name', '')) or 'unknown CA'}"
                        + (f", valid from {not_before:%Y-%m-%d}" if not_before else "")
                    ),
                    data={
                        "common_name": common_name,
                        "san_entries": sorted(_hostnames(entry, base))[:50],
                        "issuer": _issuer_name(entry.get("issuer_name", "")),
                        "not_before": not_before.isoformat() if not_before else None,
                        "not_after": not_after.isoformat() if not_after else None,
                        "serial_number": str(entry.get("serial_number") or "")[:64],
                        "crtsh_id": entry.get("id"),
                    },
                    source_url=(
                        f"https://crt.sh/?id={entry['id']}" if entry.get("id") else raw.source_url
                    ),
                    confidence=self.default_confidence,
                    confidence_reasons=[
                        "Recorded in a public Certificate Transparency log by the issuing CA"
                    ],
                    classification=Classification.PUBLIC,
                    observed_at=not_before,
                    dedupe_key=f"ct:cert:{entry.get('id') or common_name}",
                )
            )

        discovered = sorted(name for name in hostnames if name != base)[:MAX_SUBDOMAINS]
        for hostname in discovered:
            findings.append(
                FindingDraft(
                    kind=FindingKind.SUBDOMAIN,
                    title=hostname,
                    summary=f"Subdomain of {base} named in a public certificate",
                    data={
                        "hostname": hostname,
                        "parent_domain": base,
                        "wildcard": hostname.startswith("*."),
                        "discovery_method": "certificate_transparency",
                    },
                    source_url=raw.source_url,
                    # A CA published this name; the host may no longer resolve.
                    confidence=0.8,
                    confidence_reasons=[
                        "Named in a certificate recorded in a public CT log",
                        "CT entries prove issuance, not that the host is currently live",
                    ],
                    classification=Classification.PUBLIC,
                    dedupe_key=f"ct:host:{hostname}",
                )
            )

        if issuers:
            findings.append(
                FindingDraft(
                    kind=FindingKind.CERTIFICATE,
                    title=f"Certificate issuance summary for {base}",
                    summary=(
                        f"{len(entries)} certificate(s) from {len(issuers)} issuer(s); "
                        f"{len(discovered)} distinct hostname(s)"
                    ),
                    data={
                        "domain": base,
                        "certificate_count": len(entries),
                        "issuers": dict(sorted(issuers.items(), key=lambda kv: -kv[1])),
                        "hostname_count": len(discovered),
                        "earliest_issuance": earliest.isoformat() if earliest else None,
                    },
                    source_url=raw.source_url,
                    confidence=0.85,
                    confidence_reasons=["Aggregated from public CT log entries"],
                    classification=Classification.PUBLIC,
                    observed_at=earliest,
                    dedupe_key=f"ct:summary:{base}",
                )
            )
        return findings


def _hostnames(entry: dict[str, Any], base: str) -> set[str]:
    """Collect the hostnames a CT entry covers, restricted to ``base``."""
    raw_names = str(entry.get("name_value") or "")
    candidates = {
        name.strip().lower().rstrip(".")
        for name in raw_names.replace(",", "\n").split("\n")
        if name.strip()
    }
    common = str(entry.get("common_name") or "").strip().lower().rstrip(".")
    if common:
        candidates.add(common)
    suffix = f".{base}"
    return {
        name
        for name in candidates
        if name and (name == base or name.endswith(suffix) or name == f"*.{base}")
    }


def _issuer_name(issuer: str) -> str:
    """Pull the organisation or common name out of an X.509 issuer string."""
    for key in ("O=", "CN="):
        for part in str(issuer).split(","):
            part = part.strip()
            if part.startswith(key):
                return part[len(key) :].strip().strip('"')[:120]
    return str(issuer)[:120]


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(str(value))
    except (ValueError, TypeError):
        try:
            return date_parser.parse(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
