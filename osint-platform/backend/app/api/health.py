"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.core.db import get_engine
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Return immediately if the process is alive."""
    settings = get_settings()
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
def ready(response: Response) -> ReadinessResponse:
    """Check every backing service the API needs to serve traffic."""
    checks: dict[str, str] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("readiness.database_failed", error_type=type(exc).__name__)
        checks["database"] = f"error: {type(exc).__name__}"

    checks["cache"] = _check_redis()

    ok = all(value == "ok" or value.startswith("skipped") for value in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if ok else "degraded", checks=checks)


def _check_redis() -> str:
    settings = get_settings()
    if not settings.cache_enabled:
        return "skipped: disabled"
    if settings.is_test:
        return "skipped: test environment"
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        client.close()
    except Exception as exc:
        log.warning("readiness.cache_failed", error_type=type(exc).__name__)
        return f"error: {type(exc).__name__}"
    return "ok"
