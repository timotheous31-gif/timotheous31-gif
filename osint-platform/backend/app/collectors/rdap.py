"""RDAP collector.

RDAP is the structured, rate-limit-aware successor to WHOIS, so the platform
uses it rather than scraping WHOIS text. Registrant details are usually
redacted by the registry for privacy reasons; when they are present the filter
downstream classifies them, and this collector never tries to defeat a privacy
service.
"""

from __future__ import annotations

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
from app.core.ratelimit import RateLimit
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget, registrable_domain

log = get_logger(__name__)

#: RDAP event names mapped to the fields investigators actually look for.
EVENT_FIELDS = {
    "registration": "created_at",
    "expiration": "expires_at",
    "last changed": "updated_at",
    "last update of rdap database": "rdap_updated_at",
    "transfer": "transferred_at",
}

#: Status values that indicate the registration is protected or contested.
NOTABLE_STATUSES = {
    "client hold",
    "server hold",
    "pending delete",
    "redemption period",
    "client transfer prohibited",
    "server transfer prohibited",
}


@register_collector
class RDAPCollector(BaseCollector):
    """Fetch registration data for a domain or IP from its RDAP service."""

    name = "rdap"
    version = "1.0.0"
    description = "Registration data (registrar, dates, status, nameservers) via RDAP."
    supported_targets = [TargetType.DOMAIN, TargetType.URL, TargetType.IP, TargetType.EMAIL]
    requires_api_key = False
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 20.0
    default_confidence = 0.9
    source_attribution = "RDAP (IANA bootstrap via rdap.org)"

    def _query_value(self, target: NormalizedTarget) -> tuple[str, str]:
        """Return ``(kind, value)`` where kind is ``domain`` or ``ip``."""
        if target.type is TargetType.IP:
            return "ip", target.value
        if target.type is TargetType.DOMAIN:
            return "domain", registrable_domain(target.value)
        if target.type is TargetType.EMAIL:
            return "domain", registrable_domain(str(target.attributes.get("domain", "")))
        host = str(target.attributes.get("host", ""))
        return "domain", registrable_domain(host)

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        kind, value = self._query_value(target)
        if not value:
            raise CollectorError(f"Cannot derive an RDAP query from {target.value!r}")

        url = f"{self.settings.rdap_bootstrap_url.rstrip('/')}/{kind}/{value}"
        response = await http.get(
            url,
            provider=self.name,
            headers={"Accept": "application/rdap+json, application/json"},
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )

        result = CollectorResult(
            stats={"query": value, "kind": kind, "status": response.status_code}
        )
        if response.status_code == 404:
            result.notes.append(f"No RDAP record published for {value}")
            return result
        if not response.ok:
            raise CollectorError(
                f"RDAP lookup for {value} returned HTTP {response.status_code}",
                detail={"url": url},
            )

        try:
            document = response.json()
        except ValueError as exc:
            raise CollectorError(f"RDAP response for {value} was not valid JSON") from exc

        payload = RawPayload(
            source_url=url,
            content=document,
            content_type="application/rdap+json",
            status_code=response.status_code,
        )
        for draft in self.normalize(payload, target):
            result.add(draft, payload)
        result.stats["findings"] = len(result.findings)
        return result

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        document: dict[str, Any] = raw.content
        if not isinstance(document, dict):
            return []

        handle = document.get("ldhName") or document.get("handle") or document.get("name") or ""
        statuses = [str(item).lower() for item in document.get("status", []) if item]
        dates = _events(document.get("events", []))
        nameservers = sorted(
            {
                str(ns.get("ldhName", "")).lower().rstrip(".")
                for ns in document.get("nameservers", [])
                if isinstance(ns, dict) and ns.get("ldhName")
            }
        )
        registrar, registrant_org, privacy_protected = _entities(document.get("entities", []))

        data: dict[str, Any] = {
            "handle": handle,
            "registrar": registrar,
            "statuses": statuses,
            "notable_statuses": sorted(set(statuses) & NOTABLE_STATUSES),
            "nameservers": nameservers,
            "privacy_protected": privacy_protected,
            **dates,
        }
        if registrant_org and not privacy_protected:
            # Only an organisation name — never a postal address or a person.
            data["registrant_organization"] = registrant_org

        reasons = ["Published by the authoritative registry over RDAP"]
        if privacy_protected:
            reasons.append("Registrant details are redacted by a privacy service; not retrieved")

        observed = dates.get("created_at")
        summary_parts = [part for part in (registrar, dates.get("created_at")) if part]
        summary = f"Registration for {handle or target.value}"
        if summary_parts:
            summary += " — " + ", ".join(str(part) for part in summary_parts)

        findings = [
            FindingDraft(
                kind=FindingKind.DOMAIN_REGISTRATION,
                title=f"RDAP registration for {handle or target.value}",
                summary=summary,
                data=data,
                source_url=raw.source_url,
                confidence=self.default_confidence,
                confidence_reasons=reasons,
                classification=Classification.PUBLIC,
                observed_at=_parse_date(observed),
                dedupe_key=f"rdap:{handle or target.value}",
            )
        ]
        return findings


def _events(events: list) -> dict[str, str]:
    """Map RDAP events onto named date fields."""
    dates: dict[str, str] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction", "")).lower()
        date = event.get("eventDate")
        field = EVENT_FIELDS.get(action)
        if field and date:
            dates[field] = str(date)
    return dates


def _entities(entities: list) -> tuple[str | None, str | None, bool]:
    """Extract the registrar name, the registrant organisation and privacy state.

    vCard arrays are walked for the ``fn``/``org`` properties only; nothing
    resembling a postal address, phone number or personal email is read.
    """
    registrar: str | None = None
    registrant_org: str | None = None
    privacy_protected = False

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        roles = {str(role).lower() for role in entity.get("roles", [])}
        name = _vcard_value(entity.get("vcardArray"), "fn")
        org = _vcard_value(entity.get("vcardArray"), "org")

        if "registrar" in roles:
            registrar = name or entity.get("handle")
        if "registrant" in roles:
            candidate = org or name
            if candidate and _looks_redacted(candidate):
                privacy_protected = True
            else:
                registrant_org = candidate
        if any(
            "redacted" in str(remark.get("title", "")).lower()
            for remark in entity.get("remarks", [])
            if isinstance(remark, dict)
        ):
            privacy_protected = True

    return registrar, registrant_org, privacy_protected


def _vcard_value(vcard: object, prop: str) -> str | None:
    """Read one property out of an RDAP jCard array."""
    if not isinstance(vcard, list) or len(vcard) < 2 or not isinstance(vcard[1], list):
        return None
    for entry in vcard[1]:
        if isinstance(entry, list) and len(entry) >= 4 and entry[0] == prop:
            value = entry[3]
            if isinstance(value, list):
                value = " ".join(str(part) for part in value if part)
            text = str(value).strip()
            if text:
                return text
    return None


def _looks_redacted(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "redacted",
            "privacy",
            "not disclosed",
            "data protected",
            "withheld",
            "whoisguard",
            "domains by proxy",
        )
    )


def _parse_date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(str(value))
    except (ValueError, TypeError):
        try:
            return date_parser.parse(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
