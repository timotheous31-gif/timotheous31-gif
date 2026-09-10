"""Wikidata / Wikipedia collector.

Wikidata's MediaWiki API is public and needs no key. It is the right source for
the *notable* end of a name search: people with an encyclopaedia entry, whose
biographical details are already published deliberately and editorially.

Three bounded requests are made:

1. ``wbsearchentities`` — which items carry this name?
2. ``wbgetentities`` — which of those are humans (P31 = Q5), and what do they
   claim for employer, education, citizenship and Wikipedia article?
3. one more ``wbgetentities`` to turn the item IDs those claims reference into
   readable labels, so an affiliation reads "Example University" rather than
   "Q1234".

Step 2 is what keeps this honest: a name search matches ships, songs and
streets as readily as people, and anything that is not a human is dropped
rather than offered as a candidate for a person.
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

SEARCH_LIMIT = 20
#: Q5 is "human". Anything else a name search returns is not a person.
HUMAN = "Q5"
#: Claims worth resolving to labels: employer, educated at, occupation.
#: Deliberately no date of birth, no residence, no family.
AFFILIATION_PROPERTIES = ("P108", "P69")
#: P27 is *country of citizenship*, and it was being read into the candidate's
#: locations. Citizenship is a legal status; a location is where something is.
#: Conflating them let an anchor comparison match a supplied city or country
#: against a citizenship claim, which is the nationality inference this platform
#: will not make — in the anchor engine, of all places.
#:
#: So it is kept, because Wikidata genuinely publishes it about public figures,
#: but as an explicitly source-claimed citizenship fact that feeds *nothing*: it
#: is not a location, not an affiliation, and not an anchor. It is displayed with
#: its source and its limit, and that is all.
CITIZENSHIP_PROPERTY = "P27"
#: No property is read as a location. Wikidata's place claims that *are* places
#: (P19 birthplace, P551 residence) are deliberately not collected at all.
LOCATION_PROPERTIES: tuple[str, ...] = ()
OCCUPATION_PROPERTY = "P106"

#: Carried on every citizenship fact, so the limit travels with the data.
CITIZENSHIP_INTERPRETATION = (
    "Wikidata publishes this as a country-of-citizenship claim about a public figure. "
    "It is that source's claim, recorded as stated. It is not a location, not a "
    "residence, and nothing here infers a nationality from a name, a place or a "
    "language."
)


@register_collector
class WikidataCollector(PersonSourceCollector):
    """Find Wikidata items about humans carrying a name."""

    name = "wikidata"
    version = "1.0.0"
    description = "Wikidata/Wikipedia entries for notable people matching a name."
    source_label = "Wikidata"
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 20.0
    run_timeout = 90.0
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.2
    source_attribution = "Wikidata MediaWiki API (public, no key required)"
    free_access_note = "Wikidata's API is public and needs no key or account."

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        api = self.settings.wikidata_api_url
        search = await self._get(
            api,
            {
                "action": "wbsearchentities",
                "search": name,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": SEARCH_LIMIT,
                "format": "json",
            },
        )
        hits = search.get("search") or []
        ids = [str(hit["id"]) for hit in hits if isinstance(hit, dict) and hit.get("id")]
        if not ids:
            return [], []

        entities_payload = await self._get(
            api,
            {
                "action": "wbgetentities",
                "ids": "|".join(ids[:SEARCH_LIMIT]),
                "props": "claims|descriptions|labels|sitelinks/urls",
                "languages": "en",
                "sitefilter": "enwiki",
                "format": "json",
            },
        )
        entities = entities_payload.get("entities") or {}
        raw = RawPayload(source_url=f"{api}?action=wbgetentities", content=entities_payload)

        humans = {
            qid: entity
            for qid, entity in entities.items()
            if isinstance(entity, dict) and _is_human(entity)
        }
        notes: list[str] = []
        dropped = len(entities) - len(humans)
        if dropped > 0:
            notes.append(
                f"{dropped} Wikidata item(s) matching {name!r} are not people and were "
                f"not recorded as candidates."
            )
        if not humans:
            return [], notes

        labels = await self._resolve_labels(api, humans)
        return [self._candidate(qid, entity, labels, raw) for qid, entity in humans.items()], notes

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
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
            raise CollectorError(
                f"Wikidata returned HTTP {response.status_code} for "
                f"{params.get('action', 'request')}"
            )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _resolve_labels(self, api: str, humans: dict[str, Any]) -> dict[str, str]:
        """Turn referenced item IDs into English labels, in one request."""
        referenced: set[str] = set()
        for entity in humans.values():
            for prop in (
                *AFFILIATION_PROPERTIES,
                *LOCATION_PROPERTIES,
                CITIZENSHIP_PROPERTY,
                OCCUPATION_PROPERTY,
            ):
                referenced.update(_claim_ids(entity, prop))
        if not referenced:
            return {}

        payload = await self._get(
            api,
            {
                "action": "wbgetentities",
                "ids": "|".join(sorted(referenced)[:50]),
                "props": "labels",
                "languages": "en",
                "format": "json",
            },
        )
        labels: dict[str, str] = {}
        for qid, entity in (payload.get("entities") or {}).items():
            if isinstance(entity, dict):
                label = ((entity.get("labels") or {}).get("en") or {}).get("value")
                if label:
                    labels[qid] = str(label)
        return labels

    def _candidate(
        self,
        qid: str,
        entity: dict[str, Any],
        labels: dict[str, str],
        raw: RawPayload,
    ) -> PersonCandidate:
        label = ((entity.get("labels") or {}).get("en") or {}).get("value") or qid
        description = ((entity.get("descriptions") or {}).get("en") or {}).get("value") or ""
        article = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("url")

        affiliations = [
            labels[item]
            for prop in AFFILIATION_PROPERTIES
            for item in _claim_ids(entity, prop)
            if item in labels
        ]
        locations = [
            labels[item]
            for prop in LOCATION_PROPERTIES
            for item in _claim_ids(entity, prop)
            if item in labels
        ]
        citizenship = [
            labels[item] for item in _claim_ids(entity, CITIZENSHIP_PROPERTY) if item in labels
        ]
        occupations = [
            labels[item] for item in _claim_ids(entity, OCCUPATION_PROPERTY) if item in labels
        ]

        summary = description or "Wikidata item about a person"
        if occupations:
            summary += f" — {', '.join(occupations[:3])}"

        return PersonCandidate(
            url=f"https://www.wikidata.org/wiki/{qid}",
            name=str(label),
            summary=summary,
            identifiers={"wikidata": qid},
            affiliations=affiliations,
            locations=locations,
            extra={
                "description": description or None,
                "wikipedia_url": article,
                "occupations": occupations,
                # A stated claim, kept apart from anything the anchor engine
                # compares. It corroborates nothing and is never a location.
                "citizenship_claims": citizenship,
                "citizenship_interpretation": (CITIZENSHIP_INTERPRETATION if citizenship else None),
                # Having an encyclopaedia entry is what makes this source
                # appropriate: these are public figures by editorial consensus.
                "notability": "Has a Wikidata item"
                + (" and a Wikipedia article" if article else ""),
                # Promoted to a public website reference: an encyclopaedia
                # article about the person is published, public and citable.
                "website": article or None,
            },
            payload=raw,
        )


def _is_human(entity: dict[str, Any]) -> bool:
    return HUMAN in _claim_ids(entity, "P31")


def _claim_ids(entity: dict[str, Any], prop: str) -> list[str]:
    """The item IDs a property's claims point at."""
    ids: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop, []):
        if not isinstance(claim, dict):
            continue
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            ids.append(str(value["id"]))
    return ids
