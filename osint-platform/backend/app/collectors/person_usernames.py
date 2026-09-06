"""Public profile checks for handles the investigator already supplied.

The rule that shapes this collector: it checks handles it was *given*, and never
handles it invented. Deriving "timotheoussamar" from "Timotheous Samar" and
probing twelve platforms with it would manufacture leads about whoever actually
owns that handle, who may be a different person entirely. So with no supplied
usernames this collector has nothing to do and says so.

Each check is a single unauthenticated GET against a platform's own public
profile endpoint — the same request a browser makes for a logged-out visitor.
"""

from __future__ import annotations

import asyncio

from app.collectors.base import (
    CollectorConfiguration,
    CollectorContext,
    CollectorResult,
    RawPayload,
)
from app.collectors.person import PersonCandidate, PersonContext, PersonSourceCollector
from app.collectors.platforms import PLATFORMS, Platform
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

#: Supplied handles checked per run. A handful is a context list; fifty is a
#: crawl.
MAX_HANDLES = 5


@register_collector
class PersonUsernameCollector(PersonSourceCollector):
    """Confirm which public profiles exist for supplied handles."""

    name = "person_usernames"
    version = "1.0.0"
    description = (
        "Checks public profile pages for usernames the investigator supplied as "
        "known for the subject. Never guesses a handle from a name."
    )
    source_label = "Public profile check"
    rate_limit = RateLimit(requests=4, per_seconds=1.0, concurrency=4)
    timeout = 12.0
    run_timeout = 120.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.2
    source_attribution = "Direct requests to each platform's public profile endpoint"
    free_access_note = (
        "No key needed. Runs only when known usernames are supplied as context on "
        "the target; it never derives a handle from a person's name."
    )

    def configuration(self) -> CollectorConfiguration:
        return CollectorConfiguration(
            required_settings=[],
            configured=True,
            mode="free",
            detail=self.free_access_note,
        )

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        context = PersonContext.from_target(target)
        handles = [handle.strip().lstrip("@") for handle in context.known_usernames]
        handles = [handle for handle in handles if handle][:MAX_HANDLES]
        if not handles:
            return [], [
                "No known usernames were supplied for this target, so no profile was "
                "checked. Add them as context to have their public profiles confirmed."
            ]

        notes: list[str] = []
        if len(context.known_usernames) > MAX_HANDLES:
            notes.append(
                f"{len(context.known_usernames)} usernames were supplied; the first "
                f"{MAX_HANDLES} were checked."
            )

        jobs = [(handle, platform) for handle in handles for platform in PLATFORMS]
        outcomes = await asyncio.gather(
            *(self._check(platform, handle) for handle, platform in jobs),
            return_exceptions=True,
        )

        candidates: list[PersonCandidate] = []
        inconclusive = 0
        for (handle, platform), outcome in zip(jobs, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                inconclusive += 1
                continue
            exists, status_code, probe_url = outcome
            if not exists:
                continue
            profile_url = platform.profile_url(handle)
            candidates.append(
                PersonCandidate(
                    url=profile_url,
                    name=handle,
                    summary=(
                        f"A public {platform.display_name} profile exists for the "
                        f"supplied handle {handle!r}."
                    ),
                    identifiers={platform.key: handle},
                    handles=[handle],
                    extra={
                        "platform": platform.key,
                        "platform_name": platform.display_name,
                        "handle": handle,
                        "status_code": status_code,
                        # Existence is not identity: this says the handle is
                        # taken on this platform, not that the subject owns it.
                        "evidence": "profile_exists_for_supplied_handle",
                    },
                    payload=RawPayload(
                        source_url=probe_url,
                        content={
                            "platform": platform.key,
                            "username": handle,
                            "exists": True,
                            "status_code": status_code,
                        },
                        status_code=status_code,
                    ),
                )
            )

        if inconclusive:
            notes.append(
                f"{inconclusive} platform check(s) were inconclusive (rate limit or "
                f"error) and are neither a presence nor an absence."
            )
        if not candidates:
            notes.append(
                f"None of the supplied handles ({', '.join(handles)}) has a public "
                f"profile on the {len(PLATFORMS)} platforms checked."
            )
        return candidates, notes

    async def _check(self, platform: Platform, username: str) -> tuple[bool, int, str]:
        """One existence check. Returns ``(exists, status_code, probe_url)``."""
        probe_url = platform.probe_url(username)
        response = await http.get(
            probe_url,
            provider=self.name,
            timeout=self.timeout,
            retry=self.retry,
            max_bytes=256_000,
            cache_ttl=self.settings.cache_ttl_seconds,
            headers={"Accept": "application/json, text/html;q=0.8"},
        )
        if response.status_code in platform.missing_status:
            return False, response.status_code, probe_url
        if response.status_code not in platform.exists_status:
            # Anything else (403, 429, 5xx) is inconclusive, not a "no".
            raise CollectorError(
                f"{platform.display_name} answered HTTP {response.status_code} for a "
                f"presence check; treating as inconclusive"
            )
        if platform.missing_marker and response.text.strip() == platform.missing_marker:
            return False, response.status_code, probe_url
        return True, response.status_code, probe_url

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        result = await super().collect(target, ctx)
        result.stats["supplied_usernames"] = len(PersonContext.from_target(target).known_usernames)
        return result
