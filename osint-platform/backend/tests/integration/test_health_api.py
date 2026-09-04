"""Application wiring: the app boots and answers its probes."""

from __future__ import annotations

import httpx
import pytest

from app import __version__
from app.core.db import configure_engine
from app.main import create_app


@pytest.fixture
async def client():
    configure_engine("sqlite+pysqlite:///:memory:")
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_health_reports_version(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"


async def test_readiness_checks_database(client):
    response = await client.get("/health/ready")
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert response.status_code == 200


async def test_request_id_header_is_returned(client):
    response = await client.get("/health")
    assert response.headers["X-Request-ID"]


async def test_supplied_request_id_is_echoed(client):
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_security_headers_are_present(client):
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_openapi_document_is_served(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    assert "/health" in schema["paths"]


async def test_unknown_route_uses_error_envelope(client):
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "http_404"
    assert "message" in body
