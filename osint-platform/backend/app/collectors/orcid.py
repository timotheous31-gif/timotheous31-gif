"""ORCID public registry collector.

ORCID is a register of researcher identifiers that people opt into and control
themselves. The public API needs no account and no key, and returns only what
each researcher chose to make public.

The expanded-search endpoint answers "which public ORCID records carry this
name?" in one request, which is exactly the candidate question. One further
documented endpoint is read, for a bounded few records only: ``researcher-urls``
is the list of links a researcher chose to publish on their own ORCID record,
and it is the ORCID equivalent of the links on a GitHub profile — the person
saying, in public, where else to find them.

A link somebody publishes about themselves is provenance, not proof of
ownership. It is recorded as such and fires no confidence rule.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.collectors.base import CollectorContext, RawPayload
from app.collectors.person import (
    PersonCandidate,
    PersonContext,
    PersonSourceCollector,
    normalize_orcid,
)
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

ROWS = 25

#: Records whose published links are read. One extra documented call each, so
#: this stays small and prefers records an anchor already corroborated — the
#: ones the investigator is actually asking about.
LINK_DETAIL_LIMIT = 3


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
        candidates = [self._candidate(item, raw) for item in results if isinstance(item, dict)]

        links = await self._researcher_urls(candidates, PersonContext.from_target(target), base)
        for candidate in candidates:
            published = links.get(str(candidate.identifiers.get("orcid", "")))
            if published:
                candidate.extra["published_links"] = published
                candidate.extra["published_links_source"] = (
                    f"https://orcid.org/{candidate.identifiers['orcid']}"
                )
        if links:
            notes.append(
                f"Read the public 'also known as' links from {len(links)} ORCID record(s). "
                f"A link a researcher publishes is where they say to find them; it is "
                f"recorded as provenance and does not by itself establish that an account "
                f"is theirs."
            )
        return candidates, notes

    async def _researcher_urls(
        self, candidates: list[PersonCandidate], context: PersonContext, base: str
    ) -> dict[str, list[str]]:
        """The links a bounded few researchers published on their own records.

        Anchored records first: an ORCID iD the investigator supplied is the
        record they are asking about, and spending the extra call anywhere else
        first would be spending it on a stranger.
        """
        supplied = normalize_orcid(context.orcid or "")
        with_ids = [
            candidate
            for candidate in candidates
            if str(candidate.identifiers.get("orcid", "")).strip()
        ]
        ordered = sorted(
            with_ids,
            key=lambda candidate: str(candidate.identifiers["orcid"]).upper() != supplied,
        )
        wanted = [str(candidate.identifiers["orcid"]) for candidate in ordered[:LINK_DETAIL_LIMIT]]
        if not wanted:
            return {}

        async def fetch(orcid_id: str) -> tuple[str, list[str]]:
            try:
                response = await http.get(
                    f"{base}/v3.0/{orcid_id}/researcher-urls",
                    provider=self.name,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                    cache_ttl=self.settings.cache_ttl_seconds,
                )
            except Exception:
                # A record without published links, or a hiccup reading them,
                # is a gap in the evidence rather than a failed run.
                return orcid_id, []
            if not response.ok:
                return orcid_id, []
            body = response.json()
            if not isinstance(body, dict):
                return orcid_id, []
            return orcid_id, _published_urls(body)

        results = await asyncio.gather(*(fetch(orcid_id) for orcid_id in wanted))
        return {orcid_id: urls for orcid_id, urls in results if urls}

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
            extra={
                "works_count": works if isinstance(works, int) else None,
                # ORCID's expanded search returns `email` only for researchers
                # who chose to publish one. Absent means they did not, and no
                # address is ever constructed from a name and an institution.
                "public_email": _public_email(item),
            },
            payload=raw,
        )


#: Links read from one ORCID record. A researcher listing forty URLs is
#: publishing a bibliography, not saying where to find them.
MAX_PUBLISHED_LINKS = 10


def _published_urls(body: dict[str, Any]) -> list[str]:
    """Public http(s) links from an ORCID ``researcher-urls`` response."""
    urls: list[str] = []
    for entry in body.get("researcher-url") or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("url")
        raw = value.get("value") if isinstance(value, dict) else value
        text = str(raw or "").strip()
        if text.lower().startswith(("http://", "https://")) and text not in urls:
            urls.append(text)
        if len(urls) >= MAX_PUBLISHED_LINKS:
            break
    return urls


def _public_email(item: dict[str, Any]) -> str | None:
    """A published address from an ORCID record, when the researcher made one public.

    The field is a list in ORCID's schema and often absent entirely. Anything
    that is not a plausible address is ignored rather than repaired.
    """
    raw = item.get("email")
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        text = str(value or "").strip()
        if "@" in text and " " not in text:
            return text
    return None
