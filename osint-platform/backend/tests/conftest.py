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
    # Public social hosts used by the recon-import tests. These are only ever
    # resolved by the SSRF guard while validating a pasted URL; no test fetches
    # them.
    "www.linkedin.com": "93.184.215.14",
    "linkedin.com": "93.184.215.14",
    "www.instagram.com": "93.184.215.14",
    "www.facebook.com": "93.184.215.14",
    "www.youtube.com": "93.184.215.14",
    "www.snapchat.com": "93.184.215.14",
    "orcid.org": "93.184.215.14",
    "doi.org": "93.184.215.14",
    # Added with the X/Twitter and TikTok classifiers. Like the hosts above,
    # these are only ever resolved by the SSRF guard while validating a pasted
    # URL — no test fetches them, and both platforms are marked unfetchable.
    "x.com": "93.184.215.14",
    "www.x.com": "93.184.215.14",
    "twitter.com": "93.184.215.14",
    "www.twitter.com": "93.184.215.14",
    "mobile.twitter.com": "93.184.215.14",
    "tiktok.com": "93.184.215.14",
    "www.tiktok.com": "93.184.215.14",
    "media.licdn.example": "93.184.215.14",
}

#: Hosts that must resolve to an address the SSRF guard blocks, so the guard is
#: exercised on the name rather than short-circuited by a literal.
TEST_DNS_BLOCKED = {
    "metadata.google.internal": "169.254.169.254",
    # Not a valid IPv4 literal as far as :mod:`ipaddress` is concerned, so the
    # guard resolves it — which is the point: a shorthand that a resolver expands
    # to loopback must be blocked on the *resolved* address, not on its spelling.
    "127.1": "127.0.0.1",
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
        name = str(host).lower().rstrip(".")
        address = allowed.get(name) or TEST_DNS_BLOCKED.get(name)
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
def db_session(request):
    """A transactional SQLite session with the full schema created.

    Reuses the engine when a client fixture has already configured one.
    ``configure_engine`` *rebinds* the global engine to a brand-new in-memory
    database, so a test taking both ``api_client`` and ``db_session`` used to have
    the second silently destroy the first's data. That was survivable while the
    API was anonymous; now it deletes the signed-in user mid-test, and every
    request answers 401 for reasons that have nothing to do with what is being
    tested.
    """
    from app.core import db as db_module
    from app.core.db import configure_engine, get_session_factory
    from app.models import Base

    borrowed = db_module._engine is not None and "anonymous_client" in request.fixturenames
    engine = db_module._engine if borrowed else configure_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        if not borrowed:
            # The client fixture owns the schema it created and drops it itself.
            Base.metadata.drop_all(engine)


#: The password every test account uses. Long enough to pass the policy, and
#: obviously not a credential anybody could reuse anywhere.
TEST_PASSWORD = "an example test passphrase"


def _bootstrap_account(
    email: str = "analyst@example.com", role=None, workspace: str = "Test Workspace"
):
    """Create a user and a workspace directly, the way the CLI would.

    Not through the API, because there is no API route that creates the first
    account — that is the whole point of the bootstrap CLI, and a test fixture
    that could do it would mean the route existed.
    """
    from app.core.db import get_session_factory
    from app.models.enums import WorkspaceRole
    from app.services import accounts

    with get_session_factory()() as session:
        user = accounts.create_user(session, email=email, password=TEST_PASSWORD)
        space = accounts.create_workspace(session, name=workspace, owner=user)
        if role is not None and role is not WorkspaceRole.OWNER:
            membership = accounts.membership_for(session, user_id=user.id, workspace_id=space.id)
            # A workspace always has an owner, so a non-owner test subject gets a
            # separate owner rather than leaving the workspace ownerless.
            keeper = accounts.create_user(
                session, email=f"owner-of-{space.slug}@example.com", password=TEST_PASSWORD
            )
            accounts.add_member(session, workspace=space, user=keeper, role=WorkspaceRole.OWNER)
            membership.role = role
        session.commit()
        return str(user.id), str(space.id)


async def _sign_in(client, email: str = "analyst@example.com") -> str:
    """Sign ``client`` in and arm it with the CSRF header. Returns the token."""
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    # Every state-changing request needs the header; setting it on the client
    # means a test exercises the same path the frontend does.
    client.headers["X-CSRF-Token"] = token
    return token


@pytest.fixture
async def anonymous_client():
    """An httpx client bound to the ASGI app with a fresh in-memory database.

    Signed out. Used by the tests that are *about* authentication; everything else
    wants :func:`api_client`, which is signed in.
    """
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
async def api_client(anonymous_client):
    """The same client, signed in as an ANALYST-equivalent owner of one workspace.

    Authentication is the default for the suite rather than an opt-in, so a test
    written without thinking about it exercises the authorized path — and a route
    that accidentally became anonymous is caught by the tests that check for it,
    not missed by the hundreds that do not.
    """
    _bootstrap_account()
    await _sign_in(anonymous_client)
    return anonymous_client


@pytest.fixture
def workspace_id():
    """The signed-in caller's workspace.

    For tests that build rows straight through the ORM: a case created with no
    workspace is *unclaimed* and invisible to every API route, which is the
    intended behaviour and not what those tests are trying to exercise.
    """
    from app.core.db import get_session_factory
    from app.models.auth import Workspace

    with get_session_factory()() as session:
        space = session.query(Workspace).order_by(Workspace.created_at).first()
        assert space is not None, "sign in through api_client before using workspace_id"
        return space.id


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
