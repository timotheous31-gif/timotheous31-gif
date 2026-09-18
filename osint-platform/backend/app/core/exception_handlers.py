"""Translate domain errors into the uniform API error envelope."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    AmbiguousTargetError,
    AuthenticationRequired,
    ConfigurationError,
    ConflictError,
    CsrfError,
    MfaRequired,
    NotFoundError,
    OsintError,
    PermissionDenied,
    PolicyError,
    SSRFError,
    ThrottledError,
    TooManyRedirects,
    ValidationError,
)
from app.core.logging import get_logger, request_id_var
from app.schemas.common import ErrorResponse

log = get_logger(__name__)

_STATUS_BY_ERROR: dict[type[OsintError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ValidationError: 422,
    # A subclass of ValidationError, but the table is keyed on the exact type.
    AmbiguousTargetError: 422,
    ConflictError: status.HTTP_409_CONFLICT,
    ConfigurationError: status.HTTP_503_SERVICE_UNAVAILABLE,
    PolicyError: status.HTTP_403_FORBIDDEN,
    SSRFError: status.HTTP_400_BAD_REQUEST,
    TooManyRedirects: status.HTTP_502_BAD_GATEWAY,
    AuthenticationRequired: status.HTTP_401_UNAUTHORIZED,
    MfaRequired: status.HTTP_401_UNAUTHORIZED,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    CsrfError: status.HTTP_403_FORBIDDEN,
    ThrottledError: status.HTTP_429_TOO_MANY_REQUESTS,
}


def _envelope(code: str, message: str, detail: object | None = None) -> dict[str, object]:
    return ErrorResponse(
        code=code, message=message, detail=detail, request_id=request_id_var.get()
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers for domain, HTTP and validation errors."""

    @app.exception_handler(OsintError)
    async def _osint_error(_request: Request, exc: OsintError) -> JSONResponse:
        status_code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        if status_code >= 500:
            log.error("api.error", code=exc.code, message=exc.message)
        else:
            log.info("api.client_error", code=exc.code, message=exc.message)
        headers: dict[str, str] = {}
        if isinstance(exc, ThrottledError):
            # An explicit 429 with a Retry-After, so a client backs off by the
            # number the server chose rather than a number it guessed.
            headers["Retry-After"] = str(exc.retry_after)
        if isinstance(exc, AuthenticationRequired):
            # Names the scheme without inviting a browser password box, which
            # would fight the application's own login form.
            headers["WWW-Authenticate"] = "Cookie"
        return JSONResponse(
            status_code=status_code,
            content=_envelope(exc.code, exc.message, exc.detail),
            headers=headers or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(f"http_{exc.status_code}", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_error", "Request validation failed", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "An internal error occurred"),
        )
