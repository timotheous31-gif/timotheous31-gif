"""Pluggable external providers (search, breach exposure).

Importing this package registers every search provider. Registration lives here
rather than in :mod:`app.services.providers.search` so a provider module can
import the contract it implements without the contract importing it back.
"""

from __future__ import annotations

from app.services.providers.anthropic_web_search import AnthropicWebSearchProvider
from app.services.providers.google_wss import GoogleWebSearchServiceProvider
from app.services.providers.search import (
    PlannedQuery,
    ProviderAccounting,
    SearchBatch,
    SearchBrief,
    SearchProvider,
    SearchResult,
    available_providers,
    get_search_provider,
    provider_class,
    register_search_provider,
)

for _provider in (AnthropicWebSearchProvider, GoogleWebSearchServiceProvider):
    register_search_provider(_provider)

__all__ = [
    "AnthropicWebSearchProvider",
    "GoogleWebSearchServiceProvider",
    "PlannedQuery",
    "ProviderAccounting",
    "SearchBatch",
    "SearchBrief",
    "SearchProvider",
    "SearchResult",
    "available_providers",
    "get_search_provider",
    "provider_class",
    "register_search_provider",
]
