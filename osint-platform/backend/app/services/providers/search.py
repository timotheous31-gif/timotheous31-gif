"""Search-provider abstraction.

Scraping a search engine's HTML violates its terms and produces brittle
results, so the platform talks to documented search *APIs* instead. Which one
is used is configuration, not code: set ``SEARCH_PROVIDER`` and the matching
API key.

Every provider implements the same small interface, so adding one is a class
and a registry entry. ``NullSearchProvider`` is the honest default: with no
provider configured the collector reports itself unavailable rather than
silently returning nothing.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

from app.core import http
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.core.settings import Settings, get_settings

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One normalised search hit."""

    title: str
    url: str
    snippet: str
    rank: int
    provider: str

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower().removeprefix("www.")


class SearchProvider(abc.ABC):
    """Contract every search backend implements."""

    key: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=1, per_seconds=1.0, concurrency=1)
    #: Name of the settings attribute holding this provider's key.
    api_key_setting: ClassVar[str] = ""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def api_key(self) -> str | None:
        if not self.api_key_setting:
            return None
        secret = getattr(self.settings, self.api_key_setting, None)
        return secret.get_secret_value() if secret is not None else None

    def is_available(self) -> tuple[bool, str]:
        if not self.api_key_setting:
            return True, ""
        if not self.api_key():
            return False, (
                f"{self.display_name} search requires {self.api_key_setting.upper()} "
                f"to be set in the environment"
            )
        return True, ""

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Run ``query`` and return normalised results."""


class NullSearchProvider(SearchProvider):
    """The default: no provider configured."""

    key = "none"
    display_name = "No search provider"

    def is_available(self) -> tuple[bool, str]:
        return False, (
            "No search provider is configured. Set SEARCH_PROVIDER to brave, bing or "
            "serper and supply the matching API key."
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        raise ConfigurationError(self.is_available()[1])


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
                snippet=str(item.get("description", ""))[:500],
                rank=index,
                provider=self.key,
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
                snippet=str(item.get("snippet", ""))[:500],
                rank=index,
                provider=self.key,
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
                snippet=str(item.get("snippet", ""))[:500],
                rank=int(item.get("position", index)),
                provider=self.key,
            )
            for index, item in enumerate(items[:limit], start=1)
            if item.get("link")
        ]


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
