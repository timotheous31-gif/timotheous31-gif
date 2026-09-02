"""Shared pytest fixtures.

The suite is hermetic: it runs against SQLite (via the portable ``GUID``
column type), an in-memory cache, and mocked HTTP transports. No test touches
a real network endpoint.
"""

from __future__ import annotations

import json
import os
import pathlib
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


@pytest.fixture
def db_session():
    """A transactional SQLite session with the full schema created."""
    from app.core.db import configure_engine, get_session_factory
    from app.models import Base

    engine = configure_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
async def api_client():
    """An httpx client bound to the ASGI app with a fresh in-memory database."""
    import httpx

    from app.core.db import configure_engine
    from app.main import create_app
    from app.models import Base

    engine = configure_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    Base.metadata.drop_all(engine)


@pytest.fixture
async def case_id(api_client):
    """A created case, returned as its UUID string."""
    response = await api_client.post("/api/v1/cases", json={"name": "Example Domain Investigation"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[2] / "examples" / "fixtures"


def load_fixture(name: str):
    """Read a fixture file, parsing JSON when the name ends in ``.json``."""
    path = FIXTURE_DIR / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


@pytest.fixture
def fixture():
    """Fixture loader, injected into tests that need canned API responses."""
    return load_fixture


@pytest.fixture
def collector_ctx():
    """A collector context bound to a throwaway case id."""
    import uuid as _uuid

    from app.collectors.base import CollectorContext
    from app.core.settings import get_settings

    return CollectorContext(case_id=_uuid.uuid4(), settings=get_settings())


@pytest.fixture
def mock_http():
    """Install a respx-mockable httpx client as the shared HTTP client."""
    import httpx

    from app.core import http as http_module

    client = httpx.AsyncClient(follow_redirects=False)
    http_module.set_http_client(client)
    http_module.get_limiter().reset()
    yield
    http_module.set_http_client(None)
