"""Inbound request throttling.

Distinct from :mod:`app.core.ratelimit`, which limits what this platform sends
*out* so it stays a polite client of somebody else's API. This module limits what
callers send *in*, and exists for three different reasons:

* **Sign-in.** Without a limit, a password is only as strong as the attacker's
  patience. With one, an eight-attempt window turns an online guessing attack
  into an offline problem they do not have the hash for.
* **Expensive work.** An investigation run, a report render and a provider search
  each cost real CPU, real database work and — on a paid provider — real money. A
  single caller should not be able to spend a pilot customer's month in an
  afternoon.
* **Honest failure.** A refusal must be a refusal. The one thing this must never
  do is let a throttled search look like a search that found nothing, which is
  why the ingestion layer maps a rate limit onto SKIPPED and never onto
  ``NO_MATCH_RETURNED``.

Two design notes.

**Fixed window, not a token bucket.** A fixed window is trivially correct across
processes with one Redis ``INCR``, and its worst case — twice the limit across a
window boundary — is irrelevant at these magnitudes. A sliding window would be
more precise and would buy nothing here.

**Lockouts are bounded and never permanent.** A permanent lockout hands an
attacker a denial-of-service: guess wrong at somebody's address often enough and
they can never sign in again. The cooldown expires on its own.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

log = get_logger(__name__)

#: Prefix on every Redis key, so a shared Redis is legible and flushable.
KEY_PREFIX = "osint:throttle:"


@dataclass(frozen=True, slots=True)
class Verdict:
    """The outcome of one throttle check."""

    allowed: bool
    #: How many of the window's allowance are left after this call.
    remaining: int
    #: Seconds until the window resets. What goes in ``Retry-After``.
    retry_after: int
    limit: int

    @property
    def refused(self) -> bool:
        return not self.allowed


class ThrottleBackend(Protocol):
    """A counter keyed by string, with a per-key expiry."""

    def incr(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Increment ``key`` and return ``(count, seconds_until_reset)``."""

    def get(self, key: str) -> tuple[int, int]:
        """Read ``key`` without incrementing. ``(count, seconds_until_reset)``."""

    def reset(self, key: str) -> None:
        """Forget ``key``. Used on a successful sign-in, and by tests."""


class MemoryThrottle:
    """Per-process counters. Correct for one worker, and the test default.

    A deployment running several workers behind a proxy gets per-worker limits
    with this backend, which is looser than intended — hence the warning in
    :func:`get_throttle` when production has no Redis.
    """

    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def incr(self, key: str, window_seconds: int) -> tuple[int, int]:
        now = self._now()
        with self._lock:
            count, expires = self._counts.get(key, (0, 0.0))
            if expires <= now:
                count, expires = 0, now + window_seconds
            count += 1
            self._counts[key] = (count, expires)
            return count, max(int(expires - now), 1)

    def get(self, key: str) -> tuple[int, int]:
        now = self._now()
        with self._lock:
            count, expires = self._counts.get(key, (0, 0.0))
            if expires <= now:
                return 0, 0
            return count, max(int(expires - now), 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()


class RedisThrottle:
    """Counters shared across workers.

    Falls back to allowing the request if Redis is unreachable. That is the right
    failure direction for a throttle: refusing every request because the counter
    store is down turns a cache outage into a total outage. The fallback is
    logged, and sign-in still has the account-scoped counter that the database
    itself backs.
    """

    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)

    def incr(self, key: str, window_seconds: int) -> tuple[int, int]:
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = pipe.execute()
            if int(ttl) < 0:
                self._client.expire(key, window_seconds)
                ttl = window_seconds
            return int(count), max(int(ttl), 1)
        except Exception as exc:  # pragma: no cover - requires a Redis outage
            log.warning("throttle.redis_unavailable", error_type=type(exc).__name__)
            return 0, 0

    def get(self, key: str) -> tuple[int, int]:
        try:
            pipe = self._client.pipeline()
            pipe.get(key)
            pipe.ttl(key)
            value, ttl = pipe.execute()
            if value is None:
                return 0, 0
            return int(value), max(int(ttl), 0)
        except Exception as exc:  # pragma: no cover - requires a Redis outage
            log.warning("throttle.redis_unavailable", error_type=type(exc).__name__)
            return 0, 0

    def reset(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception as exc:  # pragma: no cover - requires a Redis outage
            log.warning("throttle.redis_unavailable", error_type=type(exc).__name__)


_backend: ThrottleBackend | None = None


def get_throttle(settings: Settings | None = None) -> ThrottleBackend:
    """The process-wide throttle backend, Redis where one is configured."""
    global _backend
    if _backend is not None:
        return _backend
    settings = settings or get_settings()
    backend: ThrottleBackend = MemoryThrottle()
    if settings.redis_url and not settings.is_test:
        try:
            backend = RedisThrottle(settings.redis_url)
            log.info("throttle.backend_selected", backend="redis")
        except Exception as exc:
            log.warning("throttle.redis_unavailable", error_type=type(exc).__name__)
            if settings.environment == "production":
                # Not fatal — a single-worker deployment is still protected — but
                # an operator running several workers needs to know their limits
                # are now per-worker.
                log.warning("throttle.per_worker_limits_only", environment="production")
    _backend = backend
    return backend


def set_throttle(backend: ThrottleBackend | None) -> None:
    """Install a backend (tests) or clear the cached one."""
    global _backend
    _backend = backend


def principal_key(kind: str, value: str) -> str:
    """A throttle key that does not store what it is throttling.

    Client addresses and email addresses are both personal data, and a throttle
    has no need of either in plaintext: it needs to know that two requests came
    from the same place. So the identifier is hashed, and a Redis anyone can read
    holds digests rather than a list of who tried to sign in.
    """
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"{KEY_PREFIX}{kind}:{digest}"


def check(
    key: str,
    *,
    limit: int,
    window_seconds: int,
    settings: Settings | None = None,
) -> Verdict:
    """Count one request against ``key`` and rule on it."""
    settings = settings or get_settings()
    if not settings.rate_limit_enabled or limit <= 0:
        return Verdict(allowed=True, remaining=limit, retry_after=0, limit=limit)
    count, reset_in = get_throttle(settings).incr(key, window_seconds)
    if count == 0:
        # The backend could not count (Redis outage). Allow, having logged.
        return Verdict(allowed=True, remaining=limit, retry_after=0, limit=limit)
    return Verdict(
        allowed=count <= limit,
        remaining=max(limit - count, 0),
        retry_after=reset_in,
        limit=limit,
    )


def peek(key: str, *, limit: int, settings: Settings | None = None) -> Verdict:
    """Rule on ``key`` without counting against it."""
    settings = settings or get_settings()
    if not settings.rate_limit_enabled or limit <= 0:
        return Verdict(allowed=True, remaining=limit, retry_after=0, limit=limit)
    count, reset_in = get_throttle(settings).get(key)
    return Verdict(
        allowed=count <= limit,
        remaining=max(limit - count, 0),
        retry_after=reset_in,
        limit=limit,
    )


def forget(key: str, *, settings: Settings | None = None) -> None:
    """Clear ``key``. Called on a successful sign-in so one typo costs nothing."""
    settings = settings or get_settings()
    if settings.rate_limit_enabled:
        get_throttle(settings).reset(key)
