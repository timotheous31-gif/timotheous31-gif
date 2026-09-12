"""Web search collector.

Runs a small set of targeted queries through whichever search API is
configured. The provider abstraction lives in ``services/providers/search.py``;
this collector only decides *what to ask* and how to normalise the answers.

With no provider configured the collector reports itself unavailable and the
engine records a SKIPPED run with that reason — it never pretends to have
searched.
"""

from __future__ import annotations

import asyncio

from app.collectors.base import (
    BaseCollector,
    CollectorConfiguration,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import register_collector
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget
from app.services.providers.search import SearchResult, get_search_provider

log = get_logger(__name__)

RESULTS_PER_QUERY = 10


@register_collector
class SearchCollector(BaseCollector):
    """Find public references to a target through a configured search API."""

    name = "search"
    version = "1.0.0"
    description = "Public web references via a configured search API (Brave, Bing or Serper)."
    supported_targets = [
        TargetType.DOMAIN,
        TargetType.ORGANIZATION,
        TargetType.PERSON,
        TargetType.USERNAME,
        TargetType.EMAIL,
        TargetType.URL,
    ]
    requires_api_key = True
    rate_limit = RateLimit(requests=1, per_seconds=1.0, concurrency=1)
    timeout = 20.0
    run_timeout = 90.0
    default_confidence = 0.5
    source_attribution = "Configured search provider API"

    def is_available(self) -> tuple[bool, str]:
        return get_search_provider(self.settings).is_available()

    def configuration(self) -> CollectorConfiguration:
        """Name the provider and the key it needs — never the key's value."""
        provider = get_search_provider(self.settings)
        configured, reason = provider.is_available()
        required = ["SEARCH_PROVIDER"]
        if provider.api_key_setting:
            required.append(provider.api_key_setting.upper())
        return CollectorConfiguration(
            required_settings=required,
            configured=configured,
            mode=provider.key,
            detail=(
                f"Operational: querying {provider.display_name} "
                f"with {provider.api_key_setting.upper()}."
                if configured
                else reason
            ),
        )

    def queries(self, target: NormalizedTarget) -> list[str]:
        """The queries to run for this target type.

        Deliberately narrow: the aim is to find where a target is *publicly
        referenced*, not to assemble a dossier on a person.
        """
        value = target.value
        if target.type is TargetType.DOMAIN:
            return [f'"{value}"', f"site:{value}"]
        if target.type is TargetType.URL:
            return [f'"{value}"']
        if target.type is TargetType.ORGANIZATION:
            display = str(target.attributes.get("display_name", value))
            return [f'"{display}"', f'"{display}" official site']
        if target.type is TargetType.PERSON:
            # Exactly one query: the name, quoted, and nothing else. Adding
            # qualifiers ("<name> address", "<name> phone", "<name> employer")
            # is how a name search turns into a dossier on a private person,
            # which this platform does not build.
            display = str(target.attributes.get("display_name", value))
            return [f'"{display}"']
        if target.type is TargetType.USERNAME:
            return [f'"{value}"']
        if target.type is TargetType.EMAIL:
            # Only the address as published; never combined with personal terms.
            return [f'"{value}"']
        return [f'"{value}"']

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        provider = get_search_provider(self.settings)
        self.ensure_available()

        queries = self.queries(target)
        result = CollectorResult(
            stats={"provider": provider.key, "queries": len(queries), "target": target.value}
        )

        responses = await asyncio.gather(
            *(provider.search(query, limit=RESULTS_PER_QUERY) for query in queries),
            return_exceptions=True,
        )

        seen: set[str] = set()
        for query, response in zip(queries, responses, strict=True):
            if isinstance(response, BaseException):
                result.notes.append(f"Query {query!r} failed: {type(response).__name__}")
                continue
            payload = RawPayload(
                source_url=f"search://{provider.key}?q={query}",
                content={
                    "provider": provider.key,
                    "query": query,
                    "results": [
                        {
                            "title": item.title,
                            "url": item.url,
                            "snippet": item.snippet,
                            "provider_position": item.provider_position,
                        }
                        for item in response
                    ],
                },
            )
            for item in response:
                if item.url in seen:
                    continue
                seen.add(item.url)
                for draft in self._normalize_result(item, query, target):
                    result.add(draft, payload)

        result.stats["results"] = len(seen)
        if not seen:
            result.notes.append(f"No search results for {target.value!r}")
        return result

    def _normalize_result(
        self, item: SearchResult, query: str, target: NormalizedTarget
    ) -> list[FindingDraft]:
        if target.type is TargetType.PERSON:
            return [self._person_candidate(item, query, target)]
        # A search hit is weak on its own: it shows a page mentions the target.
        # The small bonus for an early result applies only where the provider
        # documents its order as a relevance ranking. A provider that returns a
        # list and says nothing about its order gets no bonus, because reading one
        # into it would be inventing a signal.
        top_ranked = item.position_is_rank and (item.provider_position or 0) in (1, 2, 3)
        confidence = 0.5 if top_ranked else 0.4
        return [
            FindingDraft(
                kind=FindingKind.SEARCH_RESULT,
                title=item.title or item.url,
                summary=item.snippet or f"Search result for {query}",
                data={
                    "url": item.url,
                    "host": item.host,
                    "title": item.title,
                    "snippet": item.snippet,
                    "provider_position": item.provider_position,
                    "position_is_rank": item.position_is_rank,
                    "query": query,
                    "provider": item.provider,
                },
                source_url=item.url,
                confidence=confidence,
                confidence_reasons=[
                    _position_reason(item, query),
                    "A search hit shows a page mentions the target, not that they are related",
                ],
                classification=Classification.PUBLIC,
                dedupe_key=f"search:{item.url}",
            )
        ]

    def _person_candidate(
        self, item: SearchResult, query: str, target: NormalizedTarget
    ) -> FindingDraft:
        """One page that mentions the name — a candidate, not the person.

        Searching a name returns pages about everyone who shares it. Each hit is
        therefore recorded as its own candidate keyed on the page URL, so two
        different people with the same name never collapse into one entity. The
        confidence stays low by construction and the reasons say why.
        """
        display = str(target.attributes.get("display_name", target.value))
        return FindingDraft(
            kind=FindingKind.PERSON_CANDIDATE,
            title=item.title or item.url,
            summary=(
                f"A page mentioning the name {display!r}. This may be a different "
                f"person of the same name."
            ),
            data={
                "url": item.url,
                "host": item.host,
                "title": item.title,
                "snippet": item.snippet,
                "provider_position": item.provider_position,
                "position_is_rank": item.position_is_rank,
                "query": query,
                "provider": item.provider,
                "subject_name": display,
                "subject_value": target.value,
                # Consumed by extraction to key the candidate on the page rather
                # than on the name.
                "candidate_key": item.url,
                # Same shape the free PERSON collectors emit, so the UI renders
                # every candidate the same way whoever found it.
                "source": self.name,
                "source_label": "Web search",
                "candidate_name": item.title or item.url,
                "identifiers": {},
                "affiliations": [],
                "locations": [],
                "match_reasons": [
                    f"A search for {display!r} returned this page",
                ],
                "mismatch_reasons": [
                    "A search engine matched text on the page, which may name a "
                    "different person of the same name",
                ],
                "corroborated_by": [],
            },
            source_url=item.url,
            # Deliberately below the POSSIBLE_MATCH band: a name match is not
            # an identification, however highly the provider ranked the page.
            confidence=0.2,
            confidence_reasons=[
                _position_reason(item, query),
                "Matched on displayed name only; personal names are not unique",
                "Kept as a separate candidate until independent evidence links it",
            ],
            classification=Classification.PERSONAL,
            dedupe_key=f"person-candidate:{target.value}:{item.url}",
        )


def _position_reason(item: SearchResult, query: str) -> str:
    """How the provider returned this result, without overstating it.

    "Rank" is a claim about relevance ordering. Only a provider that documents its
    order as a ranking gets that word; one that returns an unordered list is
    described as having returned the page, and nothing more.
    """
    if item.position_is_rank and item.provider_position:
        return f"Returned at rank {item.provider_position} by {item.provider} for {query!r}"
    if item.provider_position:
        return (
            f"Returned by {item.provider} for {query!r}, at position "
            f"{item.provider_position} in a list whose order the provider does not "
            f"document as a ranking"
        )
    return f"Returned by {item.provider} for {query!r}; the provider states no ordering"
