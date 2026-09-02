"""Short-lived response cache.

Redis when it is reachable, an in-process TTL map otherwise, so the platform
works in development and in tests without a Redis server. Cached entries always
carry a TTL: the cache is a politeness and latency device, not an archive, and
nothing sensitive is retained here (raw payloads live in the evidence store,
which is subject to the privacy filter).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from app.core.logging import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)


class CacheBackend(Protocol):
    """Minimal cache interface."""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...


class MemoryCache:
    """Process-local TTL cache used as the fallback backend."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (time.time() + max(1, ttl), value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class RedisCache:
    """Redis-backed JSON cache."""

    def __init__(self, url: str, prefix: str = "osint:cache:") -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        self._prefix = prefix

    def get(self, key: str) -> Any | None:
        raw = self._client.get(self._prefix + key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            log.warning("cache.corrupt_entry", key=key)
            self.delete(key)
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._client.setex(self._prefix + key, max(1, ttl), json.dumps(value, default=str))

    def delete(self, key: str) -> None:
        self._client.delete(self._prefix + key)

    def clear(self) -> None:
        for key in self._client.scan_iter(self._prefix + "*"):
            self._client.delete(key)


_backend: CacheBackend | None = None


def get_cache() -> CacheBackend:
    """Return the process-wide cache backend, preferring Redis."""
    global _backend
    if _backend is not None:
        return _backend
    settings = get_settings()
    if settings.cache_enabled and not settings.is_test:
        try:
            backend: CacheBackend = RedisCache(settings.redis_url)
            backend.get("__probe__")
            _backend = backend
            log.info("cache.backend_selected", backend="redis")
            return _backend
        except Exception as exc:
            log.warning("cache.redis_unavailable", error_type=type(exc).__name__)
    _backend = MemoryCache()
    log.info("cache.backend_selected", backend="memory")
    return _backend


def set_cache(backend: CacheBackend | None) -> None:
    """Override the cache backend (tests, CLI)."""
    global _backend
    _backend = backend


def cache_key(*parts: object) -> str:
    """Build a stable, collision-resistant cache key from ``parts``."""
    joined = "|".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
