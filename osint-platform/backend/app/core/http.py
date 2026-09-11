"""The single outbound HTTP client used by every collector.

Centralising networking means the safety controls cannot be bypassed:

* SSRF validation of the initial URL and of **every** redirect hop;
* hard request timeout and a streamed response-size ceiling;
* per-provider token-bucket rate limiting and concurrency caps;
* retries with exponential backoff + jitter, for transient statuses only;
* optional short-lived response caching;
* structured logging of duration, status and size — never of headers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.cache import cache_key, get_cache
from app.core.errors import (
    RateLimitExceeded,
    ResponseTooLarge,
    SSRFError,
    TooManyRedirects,
)
from app.core.logging import get_logger
from app.core.ratelimit import RETRYABLE_STATUS, ProviderLimiter, RateLimit, RetryPolicy
from app.core.settings import get_settings
from app.core.ssrf import validate_url

log = get_logger(__name__)

_limiter = ProviderLimiter()
#: One client per event loop, because a client belongs to exactly one loop.
#:
#: ``httpx.AsyncClient`` binds its connection pool to the loop that created it,
#: and ``AsyncClient.is_closed`` only tracks an explicit ``aclose()`` — not
#: whether that loop is still alive. The engine runs each target under its own
#: ``asyncio.run``, so a single global client handed the second run a keep-alive
#: connection belonging to a loop that had already been closed, and the next
#: request on it raised ``RuntimeError: Event loop is closed``. Intermittent by
#: nature: it needed a connection the previous loop had actually kept alive,
#: which is why it showed up on Crossref and not on every source.
#:
#: Keying by loop rather than rebuilding one global is deliberate. The API serves
#: async endpoints on its own loop while ``POST /cases/{id}/run`` executes inline
#: in a worker thread when no Celery worker is consuming the queue — the default
#: zero-cost setup. With one global, those two loops take the client from each
#: other on every request: no connection is ever pooled, every displaced pool
#: leaks unclosed, and the engine's own teardown could ``aclose()`` a pool
#: belonging to the API's loop. A dict means each loop keeps its own pool and
#: closes only its own.
_clients: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}
#: Set when a caller injected a client (tests do, with respx). An injected client
#: serves every loop, is never rebuilt, and is the caller's to close — closing it
#: here would silently disconnect their mock transport.
_injected: httpx.AsyncClient | None = None


@dataclass(slots=True)
class HttpResponse:
    """A size-capped, already-read HTTP response."""

    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    elapsed_ms: float
    redirects: list[str] = field(default_factory=list)
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.content or b"null")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)

    def to_cacheable(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status_code": self.status_code,
            "headers": self.headers,
            "content": self.content.decode("utf-8", errors="replace"),
            "elapsed_ms": self.elapsed_ms,
            "redirects": self.redirects,
        }

    @classmethod
    def from_cached(cls, data: dict[str, Any]) -> HttpResponse:
        return cls(
            url=data["url"],
            final_url=data["final_url"],
            status_code=data["status_code"],
            headers=data["headers"],
            content=data["content"].encode("utf-8"),
            elapsed_ms=data.get("elapsed_ms", 0.0),
            redirects=data.get("redirects", []),
            from_cache=True,
        )


def get_limiter() -> ProviderLimiter:
    """Return the shared provider limiter (collectors register their limits)."""
    return _limiter


def register_provider(provider: str, limit: RateLimit) -> None:
    """Declare the rate limit for a provider key."""
    _limiter.register(provider, limit)


def _drop_dead_clients() -> None:
    """Forget clients whose loop has been closed.

    Their pools cannot be closed from here — ``aclose()`` would schedule work on
    a loop that is gone — so the reference is dropped and the transport left to
    the garbage collector. Reaching this state means a caller skipped
    :func:`close_owned_client`; it is the backstop, not the plan.
    """
    dead = [loop for loop in _clients if loop.is_closed()]
    for loop in dead:
        _clients.pop(loop, None)
    if dead:
        log.warning(
            "http.client_loop_ended",
            count=len(dead),
            reason=(
                "a client outlived its event loop and was discarded unclosed; "
                "call close_owned_client() before the loop ends"
            ),
        )


async def get_http_client() -> httpx.AsyncClient:
    """Return the ``httpx`` client belonging to the running event loop.

    Redirects are disabled at the transport level: this module follows them
    manually so each hop can be re-validated by the SSRF guard.

    An injected client (see :func:`set_http_client`) wins outright. Otherwise the
    client is per-loop: a second loop gets its own pool rather than inheriting
    one bound to a loop it does not control.
    """
    if _injected is not None:
        return _injected
    loop = asyncio.get_running_loop()
    _drop_dead_clients()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        settings = get_settings()
        client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            limits=httpx.Limits(
                max_connections=settings.http_max_concurrency,
                max_keepalive_connections=settings.http_max_concurrency,
            ),
            headers={"User-Agent": settings.http_user_agent},
        )
        _clients[loop] = client
    return client


async def close_http_client() -> None:
    """Close every client this module owns (application shutdown).

    Only the running loop's pool can actually be closed from here; any other
    loop's is dropped, with the same reasoning as :func:`_drop_dead_clients`. At
    shutdown the other loops are the finished ``asyncio.run`` calls of inline
    jobs, which closed their own pools on the way out.
    """
    global _injected
    loop = asyncio.get_running_loop()
    mine = _clients.pop(loop, None)
    if mine is not None and not mine.is_closed:
        await mine.aclose()
    _clients.clear()
    _injected = None


async def close_owned_client() -> None:
    """Close this loop's client, leaving an injected one alone.

    Called at the end of a self-contained ``asyncio.run`` so the connection pool
    is torn down inside the loop that owns it — and *only* that one, so a job
    running in a worker thread cannot close the pool the API's loop is using. A
    client a test injected is left alone: closing it would break the mock
    transport the test is still using.
    """
    if _injected is not None:
        return
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None and not client.is_closed:
        await client.aclose()


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Inject a client (tests use this with ``respx``).

    The injected client belongs to the caller: it serves every loop, is never
    rebuilt, and is never closed by :func:`close_owned_client`. Passing ``None``
    removes the override and restores per-loop clients.
    """
    global _injected
    _injected = client


async def _read_capped(response: httpx.Response, max_bytes: int, url: str) -> bytes:
    """Read a streamed response, aborting past ``max_bytes``."""
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise ResponseTooLarge(
                    f"Response from {url} declares {declared} bytes (limit {max_bytes})"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"Response from {url} exceeded {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def request(
    method: str,
    url: str,
    *,
    provider: str = "default",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    timeout: float | None = None,  # noqa: ASYNC109 - per-request budget, not a cancel scope
    max_bytes: int | None = None,
    max_redirects: int | None = None,
    retry: RetryPolicy | None = None,
    cache_ttl: int | None = None,
    validate: bool = True,
) -> HttpResponse:
    """Perform a guarded HTTP request.

    Args:
        provider: rate-limit bucket key (usually the collector name).
        cache_ttl: when set and the request is a GET, cache the response body.
        validate: run the SSRF guard (disable only for pre-validated literals
            in tests).

    Raises:
        SSRFError: the URL or a redirect hop is not permitted.
        ResponseTooLarge: the response exceeded the byte ceiling.
        RateLimitExceeded: the provider kept returning 429 after all retries.
        httpx.HTTPError: transport failures after all retries.
    """
    settings = get_settings()
    max_bytes = max_bytes or settings.http_max_response_bytes
    max_redirects = max_redirects if max_redirects is not None else settings.http_max_redirects
    policy = retry or RetryPolicy(
        attempts=settings.http_retry_attempts, base_delay=settings.http_retry_base_delay
    )
    method = method.upper()

    cache = get_cache() if (cache_ttl and method == "GET" and settings.cache_enabled) else None
    key = cache_key(method, url, sorted((params or {}).items())) if cache else ""
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            log.debug("http.cache_hit", url=url, provider=provider)
            return HttpResponse.from_cached(cached)

    client = await get_http_client()
    last_error: Exception | None = None

    for attempt in range(1, policy.attempts + 1):
        try:
            response = await _attempt(
                client=client,
                method=method,
                url=url,
                provider=provider,
                headers=headers,
                params=params,
                json_body=json_body,
                timeout=timeout or settings.http_timeout_seconds,
                max_bytes=max_bytes,
                max_redirects=max_redirects,
                validate=validate,
            )
        except (SSRFError, ResponseTooLarge, TooManyRedirects):
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt)
            log.warning(
                "http.retry",
                url=url,
                provider=provider,
                attempt=attempt,
                error_type=type(exc).__name__,
                delay=round(delay, 3),
            )
            await _sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < policy.attempts:
            retry_after = _parse_retry_after(response.header("retry-after"))
            delay = policy.delay_for(attempt, retry_after)
            log.warning(
                "http.retry_status",
                url=url,
                provider=provider,
                attempt=attempt,
                status_code=response.status_code,
                delay=round(delay, 3),
            )
            await _sleep(delay)
            continue

        if response.status_code == 429:
            raise RateLimitExceeded(
                f"{provider} rate-limited the request to {url} after {policy.attempts} attempts"
            )

        if cache is not None and response.ok:
            cache.set(key, response.to_cacheable(), cache_ttl or settings.cache_ttl_seconds)
        return response

    assert last_error is not None
    raise last_error


async def _attempt(
    *,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    provider: str,
    headers: dict[str, str] | None,
    params: dict[str, Any] | None,
    json_body: Any | None,
    timeout: float,  # noqa: ASYNC109 - per-request budget, not a cancel scope
    max_bytes: int,
    max_redirects: int,
    validate: bool,
) -> HttpResponse:
    """One request attempt, following redirects with re-validation."""
    started = time.perf_counter()
    current_url = url
    redirects: list[str] = []

    async with _limiter.slot(provider):
        for hop in range(max_redirects + 1):
            if validate:
                validate_url(current_url)
            await _limiter.acquire(provider)

            req = client.build_request(
                method if hop == 0 else _redirect_method(method),
                current_url,
                headers=headers,
                params=params if hop == 0 else None,
                json=json_body if hop == 0 else None,
                timeout=timeout,
            )
            response = await client.send(req, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        # A redirect status with no Location is not a redirect
                        # in any useful sense; return it as the response.
                        content = await _read_capped(response, max_bytes, current_url)
                        return _build(url, current_url, response, content, started, redirects)
                    if hop >= max_redirects:
                        raise TooManyRedirects(
                            f"{url} exceeded {max_redirects} redirects "
                            f"(last hop: {current_url})"
                        )
                    next_url = str(httpx.URL(current_url).join(location))
                    redirects.append(next_url)
                    current_url = next_url
                    continue
                content = await _read_capped(response, max_bytes, current_url)
            finally:
                await response.aclose()

            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log.info(
                "http.response",
                provider=provider,
                method=method,
                url=current_url,
                status_code=response.status_code,
                bytes=len(content),
                redirects=len(redirects),
                duration_ms=elapsed_ms,
            )
            return _build(url, current_url, response, content, started, redirects)

    raise TooManyRedirects(f"{url} exceeded {max_redirects} redirects")


def _build(
    original: str,
    final: str,
    response: httpx.Response,
    content: bytes,
    started: float,
    redirects: list[str],
) -> HttpResponse:
    return HttpResponse(
        url=original,
        final_url=final,
        status_code=response.status_code,
        headers={k.lower(): v for k, v in response.headers.items()},
        content=content,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        redirects=redirects,
    )


def _redirect_method(method: str) -> str:
    """POST/PUT become GET after a redirect, mirroring browser behaviour."""
    return "GET" if method in {"POST", "PUT", "PATCH"} else method


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            from datetime import UTC, datetime

            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return None


async def _sleep(delay: float) -> None:
    await asyncio.sleep(delay)


async def get(url: str, **kwargs: Any) -> HttpResponse:
    """Convenience wrapper for ``GET``."""
    return await request("GET", url, **kwargs)


async def head(url: str, **kwargs: Any) -> HttpResponse:
    """Convenience wrapper for ``HEAD``."""
    return await request("HEAD", url, **kwargs)
