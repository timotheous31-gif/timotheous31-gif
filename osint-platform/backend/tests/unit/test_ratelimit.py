"""Token bucket and retry policy."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.ratelimit import AsyncTokenBucket, ProviderLimiter, RateLimit, RetryPolicy


async def test_bucket_allows_burst_then_throttles():
    bucket = AsyncTokenBucket(rate_per_second=10, capacity=2)
    assert await bucket.acquire() == 0.0
    assert await bucket.acquire() == 0.0
    started = time.monotonic()
    await bucket.acquire()
    assert time.monotonic() - started >= 0.05


async def test_bucket_rejects_non_positive_rate():
    with pytest.raises(ValueError, match="positive"):
        AsyncTokenBucket(0)


async def test_limiter_registers_per_provider_limits():
    limiter = ProviderLimiter()
    limiter.register("crtsh", RateLimit(requests=1, per_seconds=2.0, concurrency=1))
    assert limiter.limit_for("crtsh").rate_per_second == 0.5
    assert limiter.limit_for("unknown").requests == 5
    await limiter.acquire("crtsh")


async def test_limiter_concurrency_slot_is_bounded():
    limiter = ProviderLimiter()
    limiter.register("p", RateLimit(requests=100, per_seconds=1.0, concurrency=1))
    order: list[str] = []

    async def worker(name: str) -> None:
        async with limiter.slot("p"):
            order.append(f"start-{name}")
            await asyncio.sleep(0.01)
            order.append(f"end-{name}")

    await asyncio.gather(worker("a"), worker("b"))
    assert order == ["start-a", "end-a", "start-b", "end-b"]


def test_retry_delay_grows_and_is_capped():
    policy = RetryPolicy(attempts=5, base_delay=1.0, max_delay=4.0, jitter=False)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(9) == 4.0


def test_retry_honours_retry_after():
    policy = RetryPolicy(base_delay=1.0, max_delay=30.0, jitter=False)
    assert policy.delay_for(1, retry_after=7.5) == 7.5
    assert policy.delay_for(1, retry_after=99.0) == 30.0


def test_jitter_stays_within_bounds():
    policy = RetryPolicy(base_delay=2.0, max_delay=8.0)
    for attempt in range(1, 5):
        assert 0 <= policy.delay_for(attempt) <= 8.0


def test_rate_limit_description():
    assert RateLimit(2, 1.0, 3).describe() == "2/1s (concurrency 3)"
