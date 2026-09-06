"""ORCID public registry collector.

ORCID is a register of researcher identifiers that people opt into and control
themselves. The public API needs no account and no key, and returns only what
each researcher chose to make public.

Only the expanded-search endpoint is used: it answers "which public ORCID
records carry this name?" in one request, which is exactly the candidate
question and nothing more.
"""

from __future__ import annotations

from typing import Any

from app.collectors.base import CollectorContext, RawPayload
from app.collectors.person import PersonCandidate, PersonSourceCollector
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

ROWS = 25


@register_collector
class OrcidCollector(PersonSourceCollector):
    """Find public ORCID researcher records carrying a name."""

    name = "orcid"
    version = "1.0.0"
    description = "Public ORCID researcher records matching a person's name."
    source_label = "ORCID"
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 20.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.2
    source_attribution = "ORCID public API (pub.orcid.org)"
    free_access_note = "ORCID's public API is open: no account, no API key, no cost."

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        base = self.settings.orcid_api_url.rstrip("/")
        url = f"{base}/v3.0/expanded-search/"
        response = await http.get(
            url,
            provider=self.name,
            params={"q": f'"{name}"', "rows": ROWS},
            headers={"Accept": "application/json"},
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        if not response.ok:
            raise CollectorError(f"ORCID returned HTTP {response.status_code} for {name!r}")

        payload = response.json() or {}
        raw = RawPayload(source_url=url, content=payload, status_code=response.status_code)
        results = payload.get("expanded-result") or []
        notes: list[str] = []
        total = payload.get("num-found")
        if isinstance(total, int) and total > len(results):
            notes.append(
                f"ORCID reports {total} public records for {name!r}; the first "
                f"{len(results)} are shown."
            )
        return [self._candidate(item, raw) for item in results if isinstance(item, dict)], notes

    def _candidate(self, item: dict[str, Any], raw: RawPayload) -> PersonCandidate:
        orcid_id = str(item.get("orcid-id", "")).strip()
        given = str(item.get("given-names") or "").strip()
        family = str(item.get("family-names") or "").strip()
        full = " ".join(part for part in (given, family) if part) or orcid_id

        institutions = [
            str(entry).strip()
            for entry in (item.get("institution-name") or [])
            if str(entry).strip()
        ]
        works = item.get("works-count")

        summary = f"ORCID researcher record {orcid_id}"
        if institutions:
            summary += f", affiliated with {institutions[0]}"
        if isinstance(works, int):
            summary += f", {works} public work(s)"

        return PersonCandidate(
            url=f"https://orcid.org/{orcid_id}" if orcid_id else "",
            name=full,
            summary=summary,
            identifiers={"orcid": orcid_id} if orcid_id else {},
            affiliations=institutions,
            extra={"works_count": works if isinstance(works, int) else None},
            payload=raw,
        )
