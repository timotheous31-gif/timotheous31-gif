"""Shared pytest fixtures.

The suite is hermetic: it runs against SQLite (via the portable ``GUID``
column type), an in-memory cache, and mocked HTTP transports. No test touches
a real network endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("CACHE_ENABLED", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SEARCH_PROVIDER", "none")
os.environ.setdefault("ALLOW_PRIVATE_NETWORKS", "false")


@pytest.fixture(autouse=True)
def _settings() -> Iterator[None]:
    """Reset the settings singleton around every test."""
    from app.core.settings import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _cache() -> Iterator[None]:
    """Give every test a clean in-process cache."""
    from app.core.cache import MemoryCache, set_cache

    set_cache(MemoryCache())
    yield
    set_cache(None)
