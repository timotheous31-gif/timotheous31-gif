"""Email collector — deliberately conservative.

Permitted here: deriving the domain, checking whether that domain accepts mail
(MX), computing the Gravatar identicon URL from the address hash, and — when an
API key is configured — asking Have I Been Pwned which *breaches* an address
appears in.

Never here, by design: breach record contents, passwords, account-recovery or
password-reset probing, login attempts, mailbox verification by SMTP, or any
enrichment intended to attach a private person to an address. The HIBP
integration returns breach names and dates only; it is a "you should rotate
your credentials" signal, not a credential source.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

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
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

HIBP_ENDPOINT = "https://haveibeenpwned.com/api/v3/breachedaccount/{account}"


@register_collector
class EmailCollector(BaseCollector):
    """Domain, Gravatar and (optionally) breach-exposure summary for an address."""

    name = "email"
    version = "1.0.0"
    description = (
        "Conservative email handling: domain extraction, Gravatar hash, and "
        "breach exposure names via HIBP when a key is configured."
    )
    supported_targets = [TargetType.EMAIL]
    requires_api_key = False
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=1)
    timeout = 20.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=2, base_delay=2.0)
    default_confidence = 0.8
    source_attribution = "Address structure, Gravatar, and Have I Been Pwned (optional)"

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        address = target.value
        domain = str(target.attributes.get("domain", address.rpartition("@")[2]))
        if not domain:
            raise CollectorError(f"Cannot derive a domain from {address!r}")

        result = CollectorResult(stats={"domain": domain})
        structure_payload = RawPayload(
            source_url=f"email://{address}",
            content={"address": address, "domain": domain},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.EMAIL_DOMAIN,
                title=f"{address} uses the domain {domain}",
                summary=(
                    f"The address belongs to {domain}; investigate the domain itself for "
                    "registration and infrastructure context."
                ),
                data={
                    "address": address,
                    "domain": domain,
                    "local_part_length": len(str(target.attributes.get("local_part", ""))),
                    "gravatar_url": _gravatar_url(address),
                    "gravatar_hash": _gravatar_hash(address),
                },
                source_url=None,
                confidence=0.99,
                confidence_reasons=["Derived directly from the address itself"],
                # An address identifies a person often enough to warrant this.
                classification=Classification.PERSONAL,
                dedupe_key=f"email:domain:{address}",
            ),
            structure_payload,
        )

        breach_finding = await self._breach_exposure(address)
        if breach_finding is not None:
            result.add(*breach_finding)
        else:
            result.notes.append(
                "Breach exposure not checked: HIBP_API_KEY is not configured"
                if self.settings.hibp_api_key is None
                else "No breach exposure reported for this address"
            )
        return result

    async def _breach_exposure(self, address: str) -> tuple[FindingDraft, RawPayload] | None:
        """Ask HIBP which breaches an address appears in. Names and dates only."""
        secret = self.settings.hibp_api_key
        if secret is None:
            return None

        url = HIBP_ENDPOINT.format(account=address)
        response = await http.get(
            url,
            provider=self.name,
            params={"truncateResponse": "false"},
            headers={
                "hibp-api-key": secret.get_secret_value(),
                "Accept": "application/json",
                "User-Agent": self.settings.http_user_agent,
            },
            timeout=self.timeout,
            retry=self.retry,
        )
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise CollectorError("HIBP rejected the API key (HTTP 401)")
        if not response.ok:
            raise CollectorError(f"HIBP returned HTTP {response.status_code}")

        breaches = response.json()
        if not isinstance(breaches, list) or not breaches:
            return None

        payload = RawPayload(source_url=url, content=breaches, status_code=response.status_code)
        summary = self._summarize_breaches(breaches)
        return (
            FindingDraft(
                kind=FindingKind.EXPOSURE_SUMMARY,
                title=f"{address} appears in {len(breaches)} known breach(es)",
                summary=(
                    "Public breach corpora list this address. Only the breach names and "
                    "dates are recorded — this platform never retrieves breached "
                    "credentials or record contents."
                ),
                data=summary,
                source_url="https://haveibeenpwned.com/",
                confidence=0.85,
                confidence_reasons=[
                    "Reported by Have I Been Pwned's breach index",
                    "Indicates exposure only; no credential is retrieved or stored",
                ],
                classification=Classification.SENSITIVE,
                observed_at=_latest(breaches),
                dedupe_key=f"email:exposure:{address}",
            ),
            payload,
        )

    def _summarize_breaches(self, breaches: list) -> dict:
        """Reduce HIBP records to names, dates and data *categories*."""
        entries = []
        categories: set[str] = set()
        for breach in breaches:
            if not isinstance(breach, dict):
                continue
            classes = [str(item) for item in breach.get("DataClasses", [])]
            categories.update(classes)
            entries.append(
                {
                    "name": breach.get("Name"),
                    "domain": breach.get("Domain"),
                    "breach_date": breach.get("BreachDate"),
                    "added_date": breach.get("AddedDate"),
                    "verified": bool(breach.get("IsVerified")),
                    "data_categories": classes,
                }
            )
        return {
            "breach_count": len(entries),
            "breaches": entries,
            "exposed_data_categories": sorted(categories),
            "note": "Breach names and dates only; no records or credentials retrieved.",
        }


def _gravatar_hash(address: str) -> str:
    """Gravatar's documented identifier: the SHA-256 of the lower-cased address."""
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


def _gravatar_url(address: str) -> str:
    return f"https://www.gravatar.com/avatar/{_gravatar_hash(address)}?d=404"


def _latest(breaches: list) -> datetime | None:
    dates = []
    for breach in breaches:
        if not isinstance(breach, dict):
            continue
        raw = breach.get("BreachDate") or breach.get("AddedDate")
        if not raw:
            continue
        try:
            dates.append(date_parser.parse(str(raw)))
        except (ValueError, TypeError, OverflowError):
            continue
    return max(dates) if dates else None
