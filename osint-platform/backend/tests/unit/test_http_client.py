"""Guarded HTTP client behaviour (all traffic mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core import http as http_module
from app.core.errors import ResponseTooLarge, SSRFError
from app.core.ratelimit import RetryPolicy


@pytest.fixture(autouse=True)
def _client():
    client = httpx.AsyncClient(follow_redirects=False)
    http_module.set_http_client(client)
    http_module.get_limiter().reset()
    yield
    http_module.set_http_client(None)


@respx.mock
async def test_get_returns_capped_response():
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, text="hello", headers={"server": "nginx"})
    )
    response = await http_module.get("https://example.com/", validate=False)
    assert response.status_code == 200
    assert response.text == "hello"
    assert response.header("server") == "nginx"
    assert response.ok


@respx.mock
async def test_response_size_ceiling_is_enforced():
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(ResponseTooLarge):
        await http_module.get("https://example.com/big", validate=False, max_bytes=1000)


@respx.mock
async def test_declared_content_length_is_rejected_early():
    respx.get("https://example.com/claims").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"content-length": "999999"})
    )
    with pytest.raises(ResponseTooLarge):
        await http_module.get("https://example.com/claims", validate=False, max_bytes=1000)


@respx.mock
async def test_redirects_are_followed_and_recorded():
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(302, headers={"location": "/b"})
    )
    respx.get("https://example.com/b").mock(return_value=httpx.Response(200, text="done"))
    response = await http_module.get("https://example.com/a", validate=False)
    assert response.status_code == 200
    assert response.final_url == "https://example.com/b"
    assert response.redirects == ["https://example.com/b"]


@respx.mock
async def test_redirect_to_internal_address_is_blocked():
    respx.get("https://example.com/open").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    with pytest.raises(SSRFError):
        await http_module.get("https://example.com/open")


@respx.mock
async def test_transient_status_is_retried_then_succeeds():
    route = respx.get("https://example.com/flaky")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, text="recovered"),
    ]
    response = await http_module.get(
        "https://example.com/flaky",
        validate=False,
        retry=RetryPolicy(attempts=3, base_delay=0.001, jitter=False),
    )
    assert response.text == "recovered"
    assert route.call_count == 2


@respx.mock
async def test_transport_errors_are_retried_and_finally_raised():
    route = respx.get("https://example.com/down")
    route.side_effect = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await http_module.get(
            "https://example.com/down",
            validate=False,
            retry=RetryPolicy(attempts=2, base_delay=0.001, jitter=False),
        )
    assert route.call_count == 2


@respx.mock
async def test_client_errors_are_not_retried():
    route = respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    response = await http_module.get("https://example.com/missing", validate=False)
    assert response.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_cache_short_circuits_second_request():
    from app.core.cache import MemoryCache, set_cache

    set_cache(MemoryCache())
    route = respx.get("https://example.com/cached").mock(
        return_value=httpx.Response(200, text="value")
    )
    first = await http_module.get("https://example.com/cached", validate=False, cache_ttl=60)
    second = await http_module.get("https://example.com/cached", validate=False, cache_ttl=60)
    assert route.call_count == 1
    assert first.text == second.text == "value"
    assert second.from_cache is True


def test_retry_after_header_parsing():
    assert http_module._parse_retry_after("12") == 12.0
    assert http_module._parse_retry_after(None) is None
    assert http_module._parse_retry_after("not-a-date") is None
