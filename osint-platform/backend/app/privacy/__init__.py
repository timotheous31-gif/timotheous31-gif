"""Privacy classification, redaction and secret detection."""

from __future__ import annotations

from app.privacy.classifier import (
    Classification,
    ClassificationResult,
    at_least,
    classify_value,
    exceeds,
    max_classification,
)
from app.privacy.filter import (
    REDACTED_VALUE,
    SUPPRESSED_VALUE,
    FilterOutcome,
    PrivacyFilter,
    default_filter,
)
from app.privacy.secrets import REDACTION, SecretMatch, contains_secret, redact_text, scan_text

__all__ = [
    "REDACTED_VALUE",
    "REDACTION",
    "SUPPRESSED_VALUE",
    "Classification",
    "ClassificationResult",
    "FilterOutcome",
    "PrivacyFilter",
    "SecretMatch",
    "at_least",
    "classify_value",
    "contains_secret",
    "default_filter",
    "exceeds",
    "max_classification",
    "redact_text",
    "scan_text",
]
