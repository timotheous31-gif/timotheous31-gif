"""Pluggable external providers (search, breach exposure)."""

from __future__ import annotations

from app.services.providers.search import (
    SearchProvider,
    SearchResult,
    get_search_provider,
    register_search_provider,
)

__all__ = [
    "SearchProvider",
    "SearchResult",
    "get_search_provider",
    "register_search_provider",
]
