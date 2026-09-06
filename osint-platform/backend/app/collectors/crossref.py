"""Crossref publication collector.

Crossref's public REST API is free and unauthenticated. It indexes works rather
than people, so a candidate here is "a publication that credits this name" —
weaker than an author record, but it carries author affiliations, which are
exactly what corroborates or rules out a same-name match.

Each candidate is keyed on the work's DOI, so two papers by two different people
of the same name stay two candidates.
"""

from __future__ import annotations

from typing import Any

from app.collectors.base import CollectorContext, RawPayload
from app.collectors.person import PersonCandidate, PersonSourceCollector, _fold
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

ROWS = 20


@register_collector
class CrossrefCollector(PersonSourceCollector):
    """Find Crossref-indexed publications crediting a name."""

    name = "crossref"
    version = "1.0.0"
    description = "Publications indexed by Crossref that credit a person's name."
    source_label = "Crossref"
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 20.0
    run_timeout = 60.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.2
    source_attribution = "Crossref REST API (public, no key required)"
    free_access_note = "Crossref's REST API is public and needs no key."

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        base = self.settings.crossref_api_url.rstrip("/")
        url = f"{base}/works"
        params: dict[str, Any] = {
            "query.author": name,
            "rows": ROWS,
            "select": "DOI,title,author,container-title,issued,URL",
        }
        if self.settings.crossref_mailto:
            # Crossref's "polite pool" asks for a contact address, not a key.
            params["mailto"] = self.settings.crossref_mailto

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
            raise CollectorError(f"Crossref returned HTTP {response.status_code} for {name!r}")

        payload = response.json() or {}
        raw = RawPayload(source_url=url, content=payload, status_code=response.status_code)
        items = ((payload.get("message") or {}).get("items")) or []

        candidates: list[PersonCandidate] = []
        for item in items:
            if isinstance(item, dict):
                candidate = self._candidate(item, name, raw)
                if candidate is not None:
                    candidates.append(candidate)

        notes: list[str] = []
        if items and not candidates:
            notes.append(
                f"Crossref returned {len(items)} work(s) for {name!r}, but none credits an "
                f"author whose name matches closely enough to record as a candidate."
            )
        return candidates, notes

    def _candidate(
        self, item: dict[str, Any], name: str, raw: RawPayload
    ) -> PersonCandidate | None:
        """Build a candidate from a work, or ``None`` if no author matches.

        Crossref's author query is fuzzy and returns works by co-authors and by
        unrelated names. Recording those as candidates would be noise attributed
        to the subject, so a work only becomes a candidate when one of its
        credited authors actually carries the searched name.
        """
        wanted = _fold(name)
        authors = item.get("author") or []
        matched: dict[str, Any] | None = None
        for author in authors:
            if not isinstance(author, dict):
                continue
            full = " ".join(
                part
                for part in (str(author.get("given") or ""), str(author.get("family") or ""))
                if part
            ).strip()
            if full and _fold(full) == wanted:
                matched = author
                break
        if matched is None:
            return None

        doi = str(item.get("DOI") or "").strip()
        if not doi:
            return None

        title = " ".join(str(part) for part in (item.get("title") or []))[:300]
        journal = " ".join(str(part) for part in (item.get("container-title") or []))[:200]
        year = _year(item.get("issued"))

        affiliations = [
            str(entry.get("name")).strip()
            for entry in (matched.get("affiliation") or [])
            if isinstance(entry, dict) and str(entry.get("name") or "").strip()
        ]
        author_name = " ".join(
            part
            for part in (str(matched.get("given") or ""), str(matched.get("family") or ""))
            if part
        ).strip()
        orcid = str(matched.get("ORCID") or "").strip()

        summary = f"Credited as an author of {title!r}" if title else "Credited as an author"
        if journal:
            summary += f" in {journal}"
        if year:
            summary += f" ({year})"

        return PersonCandidate(
            url=f"https://doi.org/{doi}",
            name=author_name,
            summary=summary,
            identifiers={"doi": doi, **({"orcid": orcid.rsplit("/", 1)[-1]} if orcid else {})},
            affiliations=affiliations,
            extra={
                "work_title": title or None,
                "journal": journal or None,
                "published_year": year,
                "co_author_count": max(len(authors) - 1, 0),
            },
            payload=raw,
        )


def _year(issued: object) -> int | None:
    if not isinstance(issued, dict):
        return None
    parts = issued.get("date-parts") or []
    if parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None
