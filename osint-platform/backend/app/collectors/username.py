"""Username presence discovery.

Checks whether a handle is *taken* on a small curated set of public publishing
platforms. Two constraints shape the whole module:

* **Presence is not identity.** A hit means the name exists on that platform,
  nothing more. Findings say exactly that, the confidence contribution is
  capped well below certainty, and the correlation layer only ever links these
  with a ``SAME_USERNAME`` edge — never "the same person".
* **One request per platform.** No enumeration of variants, no scraping of
  profile contents, no authenticated requests. Platforms are checked
  concurrently but under this collector's own rate limit.
"""

from __future__ import annotations

import asyncio

from app.collectors.base import (
    BaseCollector,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.platforms import PLATFORMS, Platform
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

#: Ceiling on how much a bare username match may contribute, regardless of how
#: many platforms it is found on. Raising this would let the platform assert
#: identity from name collisions alone.
MAX_PRESENCE_CONFIDENCE = 0.6


@register_collector
class UsernameCollector(BaseCollector):
    """Check a handle against a curated list of public platforms."""

    name = "username"
    version = "1.0.0"
    description = "Checks whether a username exists on curated public platforms."
    supported_targets = [TargetType.USERNAME, TargetType.SOCIAL_PROFILE]
    requires_api_key = False
    rate_limit = RateLimit(requests=4, per_seconds=1.0, concurrency=4)
    timeout = 12.0
    run_timeout = 90.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.5
    source_attribution = "Direct requests to each platform's public profile endpoint"

    def _username(self, target: NormalizedTarget) -> str:
        if target.type is TargetType.SOCIAL_PROFILE:
            return str(target.attributes.get("handle", "")).lower()
        return target.value

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        username = self._username(target)
        if not username:
            raise CollectorError(f"Cannot derive a username from {target.value!r}")

        result = CollectorResult(stats={"username": username, "platforms": len(PLATFORMS)})
        checks = await asyncio.gather(
            *(self._check(platform, username) for platform in PLATFORMS),
            return_exceptions=True,
        )

        found = 0
        for platform, outcome in zip(PLATFORMS, checks, strict=True):
            if isinstance(outcome, BaseException):
                result.notes.append(f"{platform.display_name}: {type(outcome).__name__}")
                continue
            exists, status_code, probe_url = outcome
            payload = RawPayload(
                source_url=probe_url,
                content={
                    "platform": platform.key,
                    "username": username,
                    "exists": exists,
                    "status_code": status_code,
                },
                status_code=status_code,
            )
            if exists:
                found += 1
            for draft in self.normalize(payload, target):
                result.add(draft, payload)

        result.stats["found"] = found
        if found == 0:
            result.notes.append(
                f"{username!r} was not found on any of the {len(PLATFORMS)} checked platforms"
            )
        return result

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

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        content = raw.content
        platform_key = str(content["platform"])
        from app.collectors.platforms import PLATFORMS_BY_KEY

        platform = PLATFORMS_BY_KEY[platform_key]
        username = str(content["username"])
        exists = bool(content["exists"])
        if not exists:
            # Absence is recorded in stats, not as a finding: "no account here"
            # is rarely useful and would bloat every report.
            return []

        profile_url = platform.profile_url(username)
        confidence = min(platform.base_confidence, MAX_PRESENCE_CONFIDENCE)
        return [
            FindingDraft(
                kind=FindingKind.USERNAME_PRESENCE,
                title=f"{username} exists on {platform.display_name}",
                summary=(
                    f"The handle {username!r} is registered on {platform.display_name}. "
                    "This shows the name is taken, not who holds it."
                ),
                data={
                    "platform": platform.key,
                    "platform_name": platform.display_name,
                    "category": platform.category,
                    "username": username,
                    "profile_url": profile_url,
                    "exists": True,
                    "response_status": content["status_code"],
                },
                source_url=profile_url,
                confidence=confidence,
                confidence_reasons=[
                    f"{platform.display_name} serves a public profile for this handle",
                    "A matching username is not evidence of a shared owner; corroborate "
                    "with a self-published link before drawing any conclusion",
                ],
                classification=Classification.PUBLIC,
                dedupe_key=f"username:{platform.key}:{username}",
            )
        ]
