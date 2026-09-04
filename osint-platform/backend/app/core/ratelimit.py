"""Per-provider rate limiting and retry policy.

Two layers cooperate:

* :class:`AsyncTokenBucket` — an in-process token bucket used by the shared HTTP
  client so a single worker never bursts against a provider;
* :class:`RedisRateLimiter` — an optional cross-process limiter keyed by
  provider, so several Celery workers share one budget.

Both are conservative by design: the platform is a guest on every site it
contacts.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RateLimit:
    """Declarative rate limit attached to a collector or provider."""

    requests: int = 5
    per_seconds: float = 1.0
    #: Maximum simultaneous in-flight requests for this provider.
    concurrency: int = 4

    @property
    def rate_per_second(self) -> float:
        return self.requests / self.per_seconds if self.per_seconds else float(self.requests)

    def describe(self) -> str:
        return f"{self.requests}/{self.per_seconds:g}s (concurrency {self.concurrency})"


class AsyncTokenBucket:
    """Async token bucket. ``acquire`` sleeps until a token is available."""

    def __init__(self, rate_per_second: float, capacity: float | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self.rate = rate_per_second
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_second)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """Consume ``tokens``, waiting if necessary. Returns the wait in seconds."""
        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                delay = deficit / self.rate
            waited += delay
            await asyncio.sleep(delay)

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens


class ProviderLimiter:
    """Registry of token buckets and semaphores, one pair per provider key."""

    def __init__(self, default: RateLimit | None = None) -> None:
        self._default = default or RateLimit()
        self._buckets: dict[str, AsyncTokenBucket] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._limits: dict[str, RateLimit] = {}

    def register(self, provider: str, limit: RateLimit) -> None:
        """Declare (or replace) the limit for ``provider``."""
        self._limits[provider] = limit
        self._buckets[provider] = AsyncTokenBucket(limit.rate_per_second)
        self._semaphores[provider] = asyncio.Semaphore(limit.concurrency)

    def limit_for(self, provider: str) -> RateLimit:
        return self._limits.get(provider, self._default)

    def _ensure(self, provider: str) -> tuple[AsyncTokenBucket, asyncio.Semaphore]:
        if provider not in self._buckets:
            self.register(provider, self._limits.get(provider, self._default))
        return self._buckets[provider], self._semaphores[provider]

    async def acquire(self, provider: str) -> float:
        bucket, _ = self._ensure(provider)
        return await bucket.acquire()

    def slot(self, provider: str) -> asyncio.Semaphore:
        _, semaphore = self._ensure(provider)
        return semaphore

    def reset(self) -> None:
        self._buckets.clear()
        self._semaphores.clear()


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter, applied to transient failures only."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 15.0
    jitter: bool = True

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Delay before ``attempt`` (1-based). Honours ``Retry-After`` when given."""
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.max_delay)
        raw = min(self.base_delay * (2 ** max(0, attempt - 1)), self.max_delay)
        if not self.jitter:
            return raw
        return random.uniform(0, raw)  # noqa: S311 - jitter, not cryptography


#: Statuses worth retrying: rate limiting and transient server-side failures.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
