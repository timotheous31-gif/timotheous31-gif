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

## Authentication

Every endpoint except `POST /api/v1/auth/login` requires a session.

1. `POST /api/v1/auth/login` with `{"email": ..., "password": ...}`. The response
   sets an `HttpOnly` session cookie and returns a `csrf_token`.
2. Send the cookie on every request. Browsers do this automatically; `curl` needs
   `-c cookies.txt -b cookies.txt`.
3. On `POST`, `PATCH` and `DELETE`, also send the token as an `X-CSRF-Token`
   header.

Every object belongs to a workspace, and a caller sees only the workspaces they
are a member of. An object in another workspace answers **404**, not 403 — a
caller who may not have it is not told that it exists.

The first administrator is created from the command line, never by an API call:

    python -m app.cli create-admin

**Using this page against a live deployment:** sign in first with the `/auth/login`
operation below. The browser stores the session cookie, so subsequent "Try it out"
calls are authenticated — but `GET` requests only. Swagger UI does not send the
`X-CSRF-Token` header, so a state-changing call from this page is refused by
design; use `curl` or the application for those.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Register the built-in collectors once, here, rather than lazily from
    # whichever request happens to need them first. Deterministic startup
    # ordering means no request can change what an investigation would plan.
    from app.collectors.registry import load_builtin_collectors

    load_builtin_collectors()
    log.info(
        "app.startup",
        environment=settings.environment,
        version=__version__,
        search_provider=settings.search_provider,
        docs_enabled=settings.docs_enabled,
        secure_cookies=settings.cookies_secure,
        hsts=settings.hsts_enabled,
        rate_limits=settings.rate_limit_enabled,
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
        # Turned off entirely rather than merely hidden when a deployment asks:
        # /docs is the one page on this origin that runs a script, and the only
        # reason the Content-Security-Policy has an exception.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins, never a wildcard. Starlette refuses to combine "*"
        # with credentials, and production additionally refuses to start with one
        # configured — see Settings.production_problems.
        allow_origins=settings.cors_origins,
        # The session lives in a cookie, so the browser must be allowed to send
        # it. This is precisely why the origin list must stay explicit: a
        # credentialed request from an origin on this list can act as the user.
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Named rather than "*", because with credentials enabled a wildcard is
        # both refused by browsers and a wider grant than anything here needs.
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Accept"],
        # `Content-Disposition` carries the filename a report should be saved
        # under. The browser already receives it; without naming it here, script
        # on the frontend origin cannot *read* it, and a cross-origin download
        # falls back to a generic name. Exposing a filename grants no access —
        # the response itself still requires the session — it only lets the page
        # save the file under the name the server chose.
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
        max_age=600,
    )

    register_exception_handlers(app)

    app.include_router(health.router)

    from app.api.router import build_api_router

    app.include_router(build_api_router(), prefix=settings.api_prefix)

    return app


app = create_app()
