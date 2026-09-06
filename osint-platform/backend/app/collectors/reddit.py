"""Reddit public search, where it is accessible.

Reddit's ``/search.json`` endpoints are public and need no key, but Reddit
increasingly refuses unauthenticated requests from datacentre addresses. That
refusal is a fact about the deployment, not a bug, so it is reported as a
SKIPPED run naming the reason rather than as a failure or — worse — as an empty
result that reads like "nothing was found".

Only public search is used. Nothing here logs in, and no private, quarantined
or authenticated content is requested.
"""

from __future__ import annotations

from typing import Any

from app.collectors.base import CollectorContext, RawPayload
from app.collectors.person import PersonCandidate, PersonSourceCollector
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError, CollectorUnavailable, RateLimitExceeded
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

LIMIT = 25


@register_collector
class RedditCollector(PersonSourceCollector):
    """Find public Reddit accounts whose name matches, where Reddit allows it."""

    name = "reddit"
    version = "1.0.0"
    description = "Public Reddit account search for a person's name, where accessible."
    source_label = "Reddit"
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=1)
    timeout = 20.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=1, base_delay=2.0)
    default_confidence = 0.2
    source_attribution = "Reddit public search (no authentication)"
    free_access_note = (
        "Reddit's public search needs no key, but Reddit often refuses "
        "unauthenticated requests from server networks; the run is then recorded "
        "as skipped with that reason."
    )

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        base = self.settings.reddit_base_url.rstrip("/")
        url = f"{base}/search.json"
        try:
            response = await http.get(
                url,
                provider=self.name,
                params={"q": name, "type": "user", "limit": LIMIT},
                headers={"Accept": "application/json"},
                timeout=self.timeout,
                retry=self.retry,
                cache_ttl=self.settings.cache_ttl_seconds,
            )
        except RateLimitExceeded as exc:
            # The shared client turns a 429 into this before the status is
            # visible here. For Reddit it means the same thing as a 403: the
            # anonymous endpoint is not available to this deployment now.
            raise CollectorUnavailable(
                "Reddit refused the unauthenticated public search (rate limited). "
                "Reddit throttles anonymous API access from many server networks; "
                "results from other free sources are unaffected."
            ) from exc

        if response.status_code in {401, 403}:
            raise CollectorUnavailable(
                f"Reddit refused the unauthenticated public search (HTTP "
                f"{response.status_code}). Reddit blocks anonymous API access from many "
                f"server networks; results from other free sources are unaffected."
            )
        if not response.ok:
            raise CollectorError(f"Reddit returned HTTP {response.status_code} for {name!r}")

        payload = response.json() or {}
        raw = RawPayload(source_url=url, content=payload, status_code=response.status_code)
        children = ((payload.get("data") or {}).get("children")) or []

        candidates: list[PersonCandidate] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            data = child.get("data")
            if isinstance(data, dict) and data.get("name"):
                candidates.append(self._candidate(data, raw))
        return candidates, []

    def _candidate(self, data: dict[str, Any], raw: RawPayload) -> PersonCandidate:
        handle = str(data.get("name", ""))
        karma = data.get("total_karma") or data.get("link_karma")

        summary = f"Public Reddit account u/{handle}"
        if isinstance(karma, int):
            summary += f" with {karma} karma"

        return PersonCandidate(
            url=f"https://www.reddit.com/user/{handle}",
            # Reddit handles are chosen, not names. Presenting the handle as the
            # candidate's "name" avoids implying Reddit confirmed a real name.
            name=handle,
            summary=summary,
            identifiers={"reddit": handle},
            handles=[handle],
            extra={
                "handle": handle,
                "karma": karma if isinstance(karma, int) else None,
                "account_created_utc": data.get("created_utc"),
                # A username is not a name: this source can only corroborate
                # through a handle the investigator already supplied.
                "name_evidence": "handle_only",
            },
            payload=raw,
        )
