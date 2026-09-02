"""Internet Archive (Wayback Machine) collector.

Establishes when a site first appeared and how its presence changed over time.
The CDX API is queried with a bounded ``limit`` and results are collapsed by
timestamp — the platform never bulk-downloads an archive.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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

#: Snapshots requested per target. Enough to date a site, far short of a mirror.
SNAPSHOT_LIMIT = 200


@register_collector
class WaybackCollector(BaseCollector):
    """Summarise a target's coverage in the Internet Archive."""

    name = "wayback"
    version = "1.0.0"
    description = "First and recent Internet Archive snapshots for a domain or URL."
    supported_targets = [TargetType.DOMAIN, TargetType.URL]
    requires_api_key = False
    rate_limit = RateLimit(requests=1, per_seconds=1.0, concurrency=1)
    timeout = 30.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=2, base_delay=1.5)
    default_confidence = 0.85
    source_attribution = "Internet Archive Wayback Machine (CDX API)"

    def _query(self, target: NormalizedTarget) -> str:
        if target.type is TargetType.URL:
            return target.value.split("://", 1)[-1]
        return f"{target.value}/*"

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        query = self._query(target)
        url = f"{self.settings.wayback_base_url.rstrip('/')}/cdx/search/cdx"
        response = await http.get(
            url,
            provider=self.name,
            params={
                "url": query,
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype,digest",
                "collapse": "timestamp:6",
                "limit": SNAPSHOT_LIMIT,
                "filter": "statuscode:200",
            },
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )

        result = CollectorResult(stats={"query": query, "status": response.status_code})
        if not response.ok:
            raise CollectorError(
                f"Wayback CDX returned HTTP {response.status_code} for {query}",
                detail={"url": url},
            )
        if not response.content.strip():
            result.notes.append(f"No Internet Archive snapshots found for {query}")
            return result

        try:
            rows = response.json()
        except ValueError as exc:
            raise CollectorError("Wayback CDX returned a response that was not valid JSON") from exc
        if not isinstance(rows, list) or len(rows) < 2:
            result.notes.append(f"No Internet Archive snapshots found for {query}")
            return result

        payload = RawPayload(
            source_url=f"{url}?url={query}&output=json",
            content=rows,
            content_type="application/json",
            status_code=response.status_code,
        )
        result.stats["snapshots"] = len(rows) - 1
        for draft in self.normalize(payload, target):
            result.add(draft, payload)
        return result

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        rows: list[list[Any]] = raw.content
        if not isinstance(rows, list) or len(rows) < 2:
            return []

        header = [str(column) for column in rows[0]]
        snapshots = [dict(zip(header, row, strict=False)) for row in rows[1:] if row]
        dated: list[tuple[dict[str, Any], datetime]] = []
        for snapshot in snapshots:
            when = _parse_timestamp(str(snapshot.get("timestamp", "")))
            if when is not None:
                dated.append((snapshot, when))
        if not dated:
            return []
        dated.sort(key=lambda item: item[1])

        first_snapshot, first_seen = dated[0]
        last_seen = dated[-1][1]
        base_url = self.settings.wayback_base_url.rstrip("/")

        findings = [
            FindingDraft(
                kind=FindingKind.ARCHIVE_SNAPSHOT,
                title=f"First archived snapshot of {target.value}",
                summary=f"Internet Archive first captured this target on {first_seen:%Y-%m-%d}",
                data={
                    "timestamp": first_snapshot.get("timestamp"),
                    "original_url": first_snapshot.get("original"),
                    "archived_url": _archive_url(base_url, first_snapshot),
                    "status_code": first_snapshot.get("statuscode"),
                    "position": "first",
                },
                source_url=_archive_url(base_url, first_snapshot),
                confidence=self.default_confidence,
                confidence_reasons=[
                    "Recorded by the Internet Archive with a capture timestamp",
                    "Archive coverage starts when a crawler first reached the site, "
                    "which may be later than the site's actual creation",
                ],
                classification=Classification.PUBLIC,
                observed_at=first_seen,
                dedupe_key=f"wayback:first:{target.value}",
            ),
            FindingDraft(
                kind=FindingKind.ARCHIVE_SNAPSHOT,
                title=f"Archive coverage of {target.value}",
                summary=(
                    f"{len(dated)} snapshot(s) between {first_seen:%Y-%m-%d} "
                    f"and {last_seen:%Y-%m-%d}"
                ),
                data={
                    "snapshot_count": len(dated),
                    "first_seen": first_seen.isoformat(),
                    "last_seen": last_seen.isoformat(),
                    "years_covered": sorted({when.year for _, when in dated}),
                    "distinct_urls": len({s.get("original") for s, _ in dated}),
                },
                source_url=raw.source_url,
                confidence=self.default_confidence,
                confidence_reasons=["Aggregated from Internet Archive CDX index entries"],
                classification=Classification.PUBLIC,
                observed_at=last_seen,
                dedupe_key=f"wayback:coverage:{target.value}",
            ),
        ]

        for snapshot, when in dated[-5:]:
            findings.append(
                FindingDraft(
                    kind=FindingKind.ARCHIVE_SNAPSHOT,
                    title=f"Snapshot of {snapshot.get('original')} on {when:%Y-%m-%d}",
                    summary=f"Archived copy captured {when:%Y-%m-%d %H:%M} UTC",
                    data={
                        "timestamp": snapshot.get("timestamp"),
                        "original_url": snapshot.get("original"),
                        "archived_url": _archive_url(base_url, snapshot),
                        "mimetype": snapshot.get("mimetype"),
                        "status_code": snapshot.get("statuscode"),
                        "position": "recent",
                    },
                    source_url=_archive_url(base_url, snapshot),
                    confidence=self.default_confidence,
                    confidence_reasons=["Recorded by the Internet Archive with a timestamp"],
                    classification=Classification.PUBLIC,
                    observed_at=when,
                    dedupe_key=f"wayback:snap:{snapshot.get('timestamp')}:"
                    f"{snapshot.get('original')}",
                )
            )
        return findings


def _archive_url(base_url: str, snapshot: dict[str, Any]) -> str:
    timestamp = snapshot.get("timestamp", "")
    original = snapshot.get("original", "")
    return f"{base_url}/web/{timestamp}/{original}"


def _parse_timestamp(value: str) -> datetime | None:
    """Wayback timestamps are ``YYYYMMDDhhmmss`` in UTC."""
    if len(value) < 8 or not value.isdigit():
        return None
    padded = value.ljust(14, "0")
    try:
        return datetime.strptime(padded, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
