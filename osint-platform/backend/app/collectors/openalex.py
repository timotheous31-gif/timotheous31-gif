"""OpenAlex author collector.

OpenAlex is an open catalogue of scholarly works and their authors, served
without an API key under a CC0 licence. Its author objects are the single most
useful free source for a name search, because they carry an institution and
often an ORCID iD — two things that can corroborate or rule out a candidate
independently of the name itself.
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

PER_PAGE = 25


@register_collector
class OpenAlexCollector(PersonSourceCollector):
    """Find OpenAlex author records carrying a name."""

    name = "openalex"
    version = "1.0.0"
    description = "Open scholarly author records (OpenAlex) matching a person's name."
    source_label = "OpenAlex"
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 20.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.2
    source_attribution = "OpenAlex API (CC0, no key required)"
    free_access_note = "OpenAlex is open data served without an API key or account."

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        base = self.settings.openalex_api_url.rstrip("/")
        url = f"{base}/authors"
        params: dict[str, Any] = {"search": name, "per-page": PER_PAGE}
        # OpenAlex asks callers to identify themselves for its faster "polite
        # pool". It is optional and is a contact address, never a credential.
        if self.settings.openalex_mailto:
            params["mailto"] = self.settings.openalex_mailto

        response = await http.get(
            url,
            provider=self.name,
            params=params,
            headers={"Accept": "application/json"},
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        if not response.ok:
            raise CollectorError(f"OpenAlex returned HTTP {response.status_code} for {name!r}")

        payload = response.json() or {}
        raw = RawPayload(source_url=url, content=payload, status_code=response.status_code)
        results = payload.get("results") or []
        notes: list[str] = []
        total = (payload.get("meta") or {}).get("count")
        if isinstance(total, int) and total > len(results):
            notes.append(
                f"OpenAlex reports {total} author records for {name!r}; the first "
                f"{len(results)} are shown."
            )
        return [self._candidate(item, raw) for item in results if isinstance(item, dict)], notes

    def _candidate(self, item: dict[str, Any], raw: RawPayload) -> PersonCandidate:
        display = str(item.get("display_name") or "").strip()
        openalex_id = str(item.get("id") or "").strip()
        orcid = str(item.get("orcid") or "").strip()
        works = item.get("works_count")
        cited = item.get("cited_by_count")

        institutions: list[str] = []
        countries: list[str] = []
        for entry in item.get("last_known_institutions") or []:
            if isinstance(entry, dict):
                _add_institution(entry, institutions, countries)
        for entry in item.get("affiliations") or []:
            if isinstance(entry, dict) and isinstance(entry.get("institution"), dict):
                _add_institution(entry["institution"], institutions, countries)

        identifiers = {"openalex": openalex_id}
        if orcid:
            identifiers["orcid"] = orcid.rsplit("/", 1)[-1]

        summary = "OpenAlex author record"
        if isinstance(works, int):
            summary += f" with {works} work(s)"
        if isinstance(cited, int):
            summary += f", cited {cited} time(s)"
        if institutions:
            summary += f"; last known institution {institutions[0]}"

        return PersonCandidate(
            url=openalex_id,
            name=display,
            summary=summary,
            identifiers={key: value for key, value in identifiers.items() if value},
            affiliations=institutions,
            locations=countries,
            extra={
                "works_count": works if isinstance(works, int) else None,
                "cited_by_count": cited if isinstance(cited, int) else None,
                # A name search over a catalogue of tens of millions of authors
                # returns look-alikes by construction; say so on the record.
                "alternate_names": [
                    str(alias) for alias in (item.get("display_name_alternatives") or [])[:5]
                ],
            },
            payload=raw,
        )


def _add_institution(entry: dict[str, Any], names: list[str], countries: list[str]) -> None:
    label = str(entry.get("display_name") or "").strip()
    if label and label not in names:
        names.append(label)
    country = str(entry.get("country_code") or "").strip()
    if country and country not in countries:
        countries.append(country)
