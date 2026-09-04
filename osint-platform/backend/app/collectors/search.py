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
                            "rank": item.rank,
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
        # A search hit is weak on its own: it shows a page mentions the target.
        confidence = 0.5 if item.rank <= 3 else 0.4
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
                    "rank": item.rank,
                    "query": query,
                    "provider": item.provider,
                },
                source_url=item.url,
                confidence=confidence,
                confidence_reasons=[
                    f"Returned at rank {item.rank} by {item.provider} for {query!r}",
                    "A search hit shows a page mentions the target, not that they are related",
                ],
                classification=Classification.PUBLIC,
                dedupe_key=f"search:{item.url}",
            )
        ]
