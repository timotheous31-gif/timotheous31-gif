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


#: Hostnames the suite is allowed to "resolve", all mapped to a documented
#: public address. Anything else raises, so a test that would have reached the
#: real network fails loudly instead of silently succeeding.
TEST_DNS = {
    "example.com": "93.184.215.14",
    "www.example.com": "93.184.215.14",
    "example.org": "93.184.215.14",
    "example.net": "93.184.215.14",
    "sub.example.com": "93.184.215.14",
    "shop.example.com": "93.184.215.14",
    "crt.sh": "93.184.215.14",
    "rdap.org": "93.184.215.14",
    "web.archive.org": "93.184.215.14",
    "api.github.com": "93.184.215.14",
    "github.com": "93.184.215.14",
    "gitlab.com": "93.184.215.14",
    "haveibeenpwned.com": "93.184.215.14",
    "api.search.brave.com": "93.184.215.14",
    "api.bing.microsoft.com": "93.184.215.14",
    "google.serper.dev": "93.184.215.14",
    "unrelated.test": "93.184.215.14",
    # Free PERSON sources.
    "pub.orcid.org": "93.184.215.14",
    "api.openalex.org": "93.184.215.14",
    "api.crossref.org": "93.184.215.14",
    "www.wikidata.org": "93.184.215.14",
    "www.reddit.com": "93.184.215.14",
    "reddit.com": "93.184.215.14",
}

#: A documented public address, used for every allowed host.
TEST_ADDRESS = "93.184.215.14"


def _allowed_hosts() -> dict[str, str]:
    """Allowed hostnames: the fixed list plus every curated platform host."""
    from urllib.parse import urlsplit

    from app.collectors.platforms import PLATFORMS

    hosts = dict(TEST_DNS)
    for platform in PLATFORMS:
        for template in (platform.url_template, platform.probe_template):
            if not template:
                continue
            host = urlsplit(template.format(username="x")).hostname
            if host:
                hosts[host.lower()] = TEST_ADDRESS
    return hosts


@pytest.fixture(autouse=True)
def _offline(monkeypatch) -> Iterator[None]:
    """Make the suite hermetic.

    Name resolution is answered from :data:`TEST_DNS` and anything else raises,
    so a collector that slips past its mock cannot quietly contact a real host.
    """
    import socket

    allowed = _allowed_hosts()

    def fake_getaddrinfo(host, port, *args, **kwargs):
        address = allowed.get(str(host).lower().rstrip("."))
        if address is None:
            raise AssertionError(
                f"Test attempted to resolve {host!r}. Mock the request, or add the "
                f"host to TEST_DNS in tests/conftest.py."
            )
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port or 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    yield


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
