"""Typed error hierarchy.

Errors are never swallowed: collectors raise, the runner records the failure on
the ``collector_runs`` row and the investigation continues with the remaining
collectors.
"""

from __future__ import annotations


class OsintError(Exception):
    """Base class for every error raised by this application."""

    #: Stable machine-readable code surfaced through the API and stored on runs.
    code = "osint_error"

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigurationError(OsintError):
    """A required setting or credential is missing or invalid."""

    code = "configuration_error"


class ValidationError(OsintError):
    """User-supplied input failed validation."""

    code = "validation_error"


class NotFoundError(OsintError):
    """A requested resource does not exist."""

    code = "not_found"


class ConflictError(OsintError):
    """The request conflicts with the current state of the resource."""

    code = "conflict"


class CollectorError(OsintError):
    """A collector failed. Recorded per-run; never aborts an investigation."""

    code = "collector_error"


class CollectorTimeout(CollectorError):
    """A collector exceeded its timeout budget."""

    code = "collector_timeout"


class CollectorUnavailable(CollectorError):
    """A collector cannot run (missing API key, provider disabled)."""

    code = "collector_unavailable"


class RateLimitExceeded(CollectorError):
    """An upstream provider signalled rate limiting after all retries."""

    code = "rate_limited"


class SSRFError(OsintError):
    """A request target resolved to a blocked address or scheme."""

    code = "ssrf_blocked"


class TooManyRedirects(OsintError):
    """An upstream redirect chain exceeded the configured hop limit."""

    code = "too_many_redirects"


class ResponseTooLarge(OsintError):
    """An upstream response exceeded the configured size ceiling."""

    code = "response_too_large"


class PolicyError(OsintError):
    """The request is refused by platform policy (robots.txt, privacy rules)."""

    code = "policy_refused"
