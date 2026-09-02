"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.settings import get_settings

log = get_logger(__name__)

DESCRIPTION = """
A privacy-first platform for **lawful** open-source intelligence work.

It collects only information that is already published by its owner or by a
public registry, using documented public APIs and protocols. Every finding is
classified by a privacy filter, redacted where necessary, and linked to
hash-verified evidence.

This API does not implement — and will not accept extensions implementing —
credential harvesting, login probing, private-account bypass, surveillance or
doxxing workflows.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info(
        "app.startup",
        environment=settings.environment,
        version=__version__,
        search_provider=settings.search_provider,
    )
    yield
    from app.core.http import close_http_client

    await close_http_client()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(health.router)

    from app.api.router import build_api_router

    app.include_router(build_api_router(), prefix=settings.api_prefix)

    return app


app = create_app()
