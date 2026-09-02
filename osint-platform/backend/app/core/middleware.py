"""HTTP middleware: request correlation ids, timing and security headers."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger, request_id_var

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

#: Conservative defaults; the API serves JSON only, never third-party HTML.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects over-large request bodies before they are parsed.

    Investigation payloads are small (a case name, a list of targets). Without
    a ceiling, an oversized body would be buffered and parsed before any
    validation ran.
    """

    def __init__(self, app: object, max_bytes: int = 1_000_000) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    return _too_large(self.max_bytes)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"code": "bad_request", "message": "Invalid Content-Length header"},
                )
        return await call_next(request)


def _too_large(limit: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "code": "request_too_large",
            "message": f"Request body exceeds the {limit} byte limit",
        },
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs the request and records its duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 128 else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.error(
                "http.request_failed",
                method=request.method,
                path=request.url.path,
                error_type=type(exc).__name__,
                duration_ms=duration_ms,
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        return response
