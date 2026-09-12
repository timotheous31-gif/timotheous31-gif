"""Search-provider abstraction.

Scraping a search engine's HTML violates its terms and produces brittle
results, so the platform talks to documented search *APIs* instead. Which one
is used is configuration, not code: set ``SEARCH_PROVIDER`` and the matching
API key.

Every provider implements the same small interface, so adding one is a class
and a registry entry. ``NullSearchProvider`` is the honest default: with no
provider configured the collector reports itself unavailable rather than
silently returning nothing.

Two facts about the world shaped the interface beyond "take a query, return
results".

**Not every provider runs the query it is handed.** Anthropic's server-side web
search decides its own queries from a brief; there is no parameter that submits
an exact query string. So a result carries both the query the platform *planned*
and the query the provider *executed*, and they are never conflated. A planned
query recorded as executed would make the execution ledger a fiction.

**Not every provider returns the same fields.** One returns a description per
result, another returns only a title and a URL. The absent field is therefore
``None`` rather than ``""``: "this provider publishes no description" and "this
provider returned an empty description" are different facts, and the correlation
engine reads the difference. Nothing here ever invents a value to fill a gap.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from app.core import http
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.core.settings import Settings, get_settings

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One normalised search hit, with everything needed to account for it.

    The provenance fields matter as much as the content. A result is discovery
    evidence — "this public page exists and a search found it" — and a report has
    to be able to say which query surfaced it and whether that query was the one
    the platform asked for.
    """

    title: str
    url: str
    provider: str
    #: The provider's own description of the page. ``None`` means this provider
    #: publishes no such field; ``""`` means it published an empty one. Nothing
    #: downstream may substitute a value for either.
    snippet: str | None = None
    #: Where the result sat in the provider's own returned list, 1-based.
    provider_position: int | None = None
    #: True only where the provider documents that its list order is a relevance
    #: ranking. Otherwise the position is an index into a list and means nothing
    #: more, and nothing may call it a rank.
    position_is_rank: bool = False
    #: The query the provider actually executed.
    query: str = ""
    #: The query the platform asked for. Equal to ``query`` on a provider that
    #: runs exactly what it is handed; deliberately separate because one does
    #: not, and recording its plan as its execution would be a lie.
    planned_query: str = ""
    #: True only when the provider executed the planned query verbatim.
    query_executed_as_planned: bool = False
    #: The spelling searched for, and how it relates to the canonical name.
    search_variant: str = ""
    variant_type: str = ""
    #: Which family of the recon plan the query belonged to.
    query_family: str = ""
    #: What the provider displayed as the source, when it supplies one. Never
    #: trusted over the URL itself — it is a label, not an address.
    displayed_url: str = ""
    #: ``web`` | ``image`` | ``news`` | ``video`` | ``document`` — only when the
    #: provider states it. Never inferred from the URL here; classification is
    #: the ingestion layer's job and it has better tools for it.
    result_type: str = ""
    #: An image the provider legitimately returned alongside the result. Treated
    #: as untrusted input and validated before anything is done with it.
    image_url: str = ""
    #: The provider's own statement of the page's age, verbatim and unparsed.
    #: Provenance about the index, not a fact about the page.
    page_age: str = ""
    #: Correlation ids a provider assigns to the retrieval itself. For the
    #: Anthropic channel these are ``server_tool_use.id`` and the paired
    #: ``web_search_tool_result.tool_use_id``; empty for providers that assign
    #: none. They let a stored result be traced back to the exact search
    #: operation inside a multi-search response.
    retrieval_call_id: str = ""
    retrieval_result_id: str = ""
    retrieved_at: datetime | None = None

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower().removeprefix("www.")

    @property
    def snippet_text(self) -> str:
        """The description as text, with no claim that one exists.

        For reading; never for deciding. Code that must distinguish "no such
        field" from "empty field" reads :attr:`snippet` directly.
        """
        return self.snippet or ""

    def with_plan(
        self,
        *,
        planned: PlannedQuery,
        retrieved_at: datetime,
        executed_query: str | None = None,
    ) -> SearchResult:
        """A copy carrying the search that found it.

        ``executed_query`` is supplied only by a provider that chooses its own
        queries; when it is ``None`` the provider ran the planned query verbatim
        and the two are recorded as equal *and* flagged as such. A provider
        adapter therefore cannot forget, or quietly overstate, its own
        provenance.
        """
        as_planned = executed_query is None
        return replace(
            self,
            query=planned.query if as_planned else executed_query or "",
            planned_query=planned.query,
            query_executed_as_planned=as_planned,
            search_variant=planned.name_variant,
            variant_type=planned.variant_type,
            query_family=planned.family,
            retrieved_at=retrieved_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """The stored form. Only fields a provider actually supplied.

        ``snippet``, ``provider_position`` and the optional labels stay ``None``
        when absent rather than becoming empty strings or zeroes, because the
        report distinguishes "not returned" from "returned empty".
        """
        return {
            "title": self.title,
            "url": self.url,
            "provider": self.provider,
            "snippet": self.snippet,
            "provider_position": self.provider_position,
            "position_is_rank": self.position_is_rank,
            "query": self.query,
            "planned_query": self.planned_query,
            "query_executed_as_planned": self.query_executed_as_planned,
            "search_variant": self.search_variant,
            "variant_type": self.variant_type,
            "query_family": self.query_family,
            "displayed_url": self.displayed_url or None,
            "result_type": self.result_type or None,
            "image_url": self.image_url or None,
            "page_age": self.page_age or None,
            "server_tool_use_id": self.retrieval_call_id or None,
            "tool_result_id": self.retrieval_result_id or None,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
        }


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    """One query the platform would like run, with its own provenance.

    Built from the recon plan. A provider may execute it verbatim or may treat it
    as a brief; either way the plan is recorded as the plan.
    """

    query: str
    family: str = ""
    name_variant: str = ""
    variant_type: str = ""


@dataclass(frozen=True, slots=True)
class SearchBrief:
    """What the platform asked a provider to look for in one investigation."""

    subject: str
    planned: tuple[PlannedQuery, ...]
    limit: int = 10
    #: Coarse locality for providers that accept one. City / region / ISO-3166-1
    #: alpha-2 country / IANA timezone only — never an address, never an IP.
    locality: dict[str, str] = field(default_factory=dict)


#: Outcomes a provider call can have, beyond returning results.
OUTCOME_OK = "ok"
OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_FAILED = "failed"


@dataclass(slots=True)
class ProviderAccounting:
    """What one provider call consumed.

    Every money figure here is an **estimate** computed from a published unit
    price and a count the provider reported. It is not an invoice, it is not
    reconciled against billing, and nothing that renders it may present it as
    one.
    """

    #: Search operations the provider reported executing.
    searches_executed: int = 0
    #: The hard ceiling the platform set for this call, where it could set one.
    search_budget: int | None = None
    #: True when the provider stopped because the ceiling was reached.
    budget_exhausted: bool = False
    #: Published price per search operation, as configured.
    unit_cost_usd: float | None = None
    #: Token counts, when the provider reports them. Tokens are billed
    #: separately and are deliberately *not* folded into the search estimate.
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""
    note: str = ""

    @property
    def estimated_search_cost_usd(self) -> float | None:
        if self.unit_cost_usd is None:
            return None
        return round(self.searches_executed * self.unit_cost_usd, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "searches_executed": self.searches_executed,
            "search_budget": self.search_budget,
            "budget_exhausted": self.budget_exhausted,
            "unit_cost_usd": self.unit_cost_usd,
            "estimated_search_cost_usd": self.estimated_search_cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model or None,
            "is_estimate": True,
            "token_cost_included": False,
            "note": self.note
            or (
                "Estimated from the configured published unit price and the count the "
                "provider reported. Not a billed total; token costs are separate."
            ),
        }


@dataclass(slots=True)
class SearchBatch:
    """Everything one provider call produced, including what it cost."""

    results: list[SearchResult] = field(default_factory=list)
    #: The queries the provider actually executed, in order.
    executed_queries: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    accounting: ProviderAccounting = field(default_factory=ProviderAccounting)
    outcome: str = OUTCOME_OK
    reason: str = ""


class SearchProvider(abc.ABC):
    """Contract every search backend implements."""

    key: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=1, per_seconds=1.0, concurrency=1)
    #: Name of the settings attribute holding this provider's key.
    api_key_setting: ClassVar[str] = ""
    #: False where the provider chooses its own queries from a brief rather than
    #: running the one it is handed. Read by the ingestion layer so a
    #: model-mediated channel is never accounted for as a deterministic one.
    runs_requested_query: ClassVar[bool] = True
    #: False where the provider returns no per-result description. Nothing
    #: substitutes one; the ingestion layer treats such results as low-context
    #: discovery candidates instead.
    supplies_snippet: ClassVar[bool] = True
    #: True where the provider documents its list order as a relevance ranking.
    position_is_rank: ClassVar[bool] = True
    #: ``ACTIVE`` or a word naming what the provider is waiting on. Anything
    #: other than ``ACTIVE`` can be configured and inspected but never runs.
    activation_state: ClassVar[str] = "ACTIVE"
    #: What the reader should know about this channel's provenance, in one line.
    provenance_note: ClassVar[str] = ""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def api_key(self) -> str | None:
        if not self.api_key_setting:
            return None
        secret = getattr(self.settings, self.api_key_setting, None)
        return secret.get_secret_value() if secret is not None else None

    def is_available(self) -> tuple[bool, str]:
        if self.activation_state != "ACTIVE":
            return False, (
                f"{self.display_name} is configured but not activated "
                f"({self.activation_state})."
            )
        if not self.api_key_setting:
            return True, ""
        if not self.api_key():
            return False, (
                f"{self.display_name} requires {self.api_key_setting.upper()} "
                f"to be set in the environment"
            )
        return True, ""

    def search_budget(self) -> int | None:
        """A hard ceiling on search operations per investigation, if any."""
        return None

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Run ``query`` and return normalised results."""

    async def discover(self, brief: SearchBrief) -> SearchBatch:
        """Work a whole brief, one planned query at a time.

        The default for every provider that runs exactly what it is handed: each
        planned query becomes one search, and each result is stamped with that
        query as both planned and executed. A provider that chooses its own
        queries overrides this and records what it really did.
        """
        batch = SearchBatch(accounting=ProviderAccounting(search_budget=self.search_budget()))
        for planned in brief.planned:
            moment = datetime.now(UTC)
            try:
                results = await self.search(planned.query, limit=brief.limit)
            except Exception as exc:
                batch.failures.append(
                    {
                        "query": planned.query,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
                log.warning(
                    "search_provider.query_failed",
                    provider=self.key,
                    error_type=type(exc).__name__,
                )
                continue
            batch.executed_queries.append(planned.query)
            batch.accounting.searches_executed += 1
            for result in results:
                batch.results.append(result.with_plan(planned=planned, retrieved_at=moment))
        if batch.failures and not batch.executed_queries:
            batch.outcome = OUTCOME_FAILED
            batch.reason = str(batch.failures[0].get("error") or "")[:300]
        return batch


class NullSearchProvider(SearchProvider):
    """The default: no provider configured."""

    key = "none"
    display_name = "No search provider"

    def is_available(self) -> tuple[bool, str]:
        return False, (
            "No search provider is configured. Set SEARCH_PROVIDER to "
            "anthropic_web_search, brave, bing or serper and supply the matching "
            "credential. Every free structured source and the manual search "
            "workflow keep working with no provider at all."
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        raise ConfigurationError(self.is_available()[1])

    async def discover(self, brief: SearchBrief) -> SearchBatch:
        return SearchBatch(outcome=OUTCOME_UNAVAILABLE, reason=self.is_available()[1])


class BraveSearchProvider(SearchProvider):
    """Brave Search API (https://api.search.brave.com)."""

    key = "brave"
    display_name = "Brave Search"
    api_key_setting = "brave_api_key"
    rate_limit = RateLimit(requests=1, per_seconds=1.0, concurrency=1)

    endpoint = "https://api.search.brave.com/res/v1/web/search"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        response = await http.get(
            self.endpoint,
            provider=f"search:{self.key}",
            params={"q": query, "count": min(limit, 20)},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key() or "",
            },
            retry=RetryPolicy(attempts=2, base_delay=1.0),
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        if not response.ok:
            raise ConfigurationError(
                f"Brave Search returned HTTP {response.status_code}",
                detail={"status": response.status_code},
            )
        payload = response.json() or {}
        items = ((payload.get("web") or {}).get("results")) or []
        return [
            SearchResult(
                title=str(item.get("title", ""))[:300],
                url=str(item.get("url", "")),
                provider=self.key,
                snippet=str(item.get("description", ""))[:500],
                provider_position=index,
                position_is_rank=True,
                query=query,
                displayed_url=str(item.get("meta_url", {}).get("netloc", "") or ""),
                image_url=_thumbnail(item),
            )
            for index, item in enumerate(items[:limit], start=1)
            if item.get("url")
        ]


class BingSearchProvider(SearchProvider):
    """Bing Web Search API."""

    key = "bing"
    display_name = "Bing Web Search"
    api_key_setting = "bing_api_key"

    endpoint = "https://api.bing.microsoft.com/v7.0/search"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        response = await http.get(
            self.endpoint,
            provider=f"search:{self.key}",
            params={"q": query, "count": min(limit, 50), "responseFilter": "Webpages"},
            headers={
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": self.api_key() or "",
            },
            retry=RetryPolicy(attempts=2, base_delay=1.0),
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        if not response.ok:
            raise ConfigurationError(f"Bing Search returned HTTP {response.status_code}")
        payload = response.json() or {}
        items = ((payload.get("webPages") or {}).get("value")) or []
        return [
            SearchResult(
                title=str(item.get("name", ""))[:300],
                url=str(item.get("url", "")),
                provider=self.key,
                snippet=str(item.get("snippet", ""))[:500],
                provider_position=index,
                position_is_rank=True,
                query=query,
                displayed_url=str(item.get("displayUrl", "") or ""),
            )
            for index, item in enumerate(items[:limit], start=1)
            if item.get("url")
        ]


class SerperSearchProvider(SearchProvider):
    """Serper.dev, a Google Search API wrapper."""

    key = "serper"
    display_name = "Serper"
    api_key_setting = "serper_api_key"

    endpoint = "https://google.serper.dev/search"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        response = await http.request(
            "POST",
            self.endpoint,
            provider=f"search:{self.key}",
            json_body={"q": query, "num": min(limit, 20)},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-KEY": self.api_key() or "",
            },
            retry=RetryPolicy(attempts=2, base_delay=1.0),
        )
        if not response.ok:
            raise ConfigurationError(f"Serper returned HTTP {response.status_code}")
        payload = response.json() or {}
        items = payload.get("organic") or []
        return [
            SearchResult(
                title=str(item.get("title", ""))[:300],
                url=str(item.get("link", "")),
                provider=self.key,
                snippet=str(item.get("snippet", ""))[:500],
                provider_position=int(item.get("position", index)),
                position_is_rank=True,
                query=query,
                displayed_url=str(item.get("displayedLink", "") or ""),
                image_url=str(item.get("imageUrl", "") or ""),
            )
            for index, item in enumerate(items[:limit], start=1)
            if item.get("link")
        ]


def _thumbnail(item: dict[str, Any]) -> str:
    """A thumbnail a provider returned, if it returned one.

    Untrusted: it comes from a third party describing a page neither of us
    controls. Nothing fetches it here — it is validated where it is used.
    """
    thumbnail = item.get("thumbnail")
    if isinstance(thumbnail, dict):
        return str(thumbnail.get("src", "") or "")
    return ""


_PROVIDERS: dict[str, type[SearchProvider]] = {}


def register_search_provider(cls: type[SearchProvider]) -> type[SearchProvider]:
    """Register a search provider under its ``key``."""
    if not cls.key:
        raise ConfigurationError(f"{cls.__name__} must declare a `key`")
    _PROVIDERS[cls.key] = cls
    return cls


for _provider in (
    NullSearchProvider,
    BraveSearchProvider,
    BingSearchProvider,
    SerperSearchProvider,
):
    register_search_provider(_provider)


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    """Instantiate the provider named by ``SEARCH_PROVIDER``."""
    settings = settings or get_settings()
    cls = _PROVIDERS.get(settings.search_provider)
    if cls is None:
        raise ConfigurationError(
            f"Unknown search provider {settings.search_provider!r}. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )
    return cls(settings)


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def provider_class(key: str) -> type[SearchProvider] | None:
    """The registered class for ``key``, without instantiating it."""
    return _PROVIDERS.get(key)


def planned_queries(queries: Sequence[Any]) -> tuple[PlannedQuery, ...]:
    """Recon queries as provider-facing planned queries.

    Takes :class:`app.services.recon.ReconQuery` without importing it, because
    the provider layer must not depend on the recon layer.
    """
    return tuple(
        PlannedQuery(
            query=str(getattr(item, "query", "")),
            family=str(getattr(item, "family", "")),
            name_variant=str(getattr(item, "name_variant", "")),
            variant_type=str(getattr(item, "variant_type", "")),
        )
        for item in queries
    )
