"""Structured logging.

Log records carry investigation context (case, target, collector, request id,
duration) and are scrubbed of anything that looks like a credential before they
are emitted.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.settings import get_settings

#: Request/job correlation id, set by middleware or the Celery task wrapper.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
target_id_var: ContextVar[str | None] = ContextVar("target_id", default=None)
collector_var: ContextVar[str | None] = ContextVar("collector", default=None)

_REDACTED = "[REDACTED]"

#: Patterns that must never reach a log sink. Deliberately broad.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|authorization|auth|cookie|"
    r"session|private[_-]?key|credential|bearer)"
)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        scrubbed = value
        for pattern in _SECRET_VALUE_PATTERNS:
            scrubbed = pattern.sub(_REDACTED, scrubbed)
        return scrubbed
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _SECRET_KEY_PATTERN.search(str(key)) else _scrub_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_scrub_value(item) for item in value]
    return value


def scrub_secrets(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor removing credential-shaped data from log events."""
    for key in list(event_dict):
        if _SECRET_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _scrub_value(event_dict[key])
    return event_dict


def add_investigation_context(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach the ambient investigation identifiers to every event."""
    for key, var in (
        ("request_id", request_id_var),
        ("case_id", case_id_var),
        ("target_id", target_id_var),
        ("collector", collector_var),
    ):
        value = var.get()
        if value is not None and key not in event_dict:
            event_dict[key] = value
    return event_dict


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    settings = get_settings()
    log_level = (level or settings.log_level).upper()
    log_format = fmt or settings.log_format

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.INFO),
        force=True,
    )
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            add_investigation_context,
            scrub_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
