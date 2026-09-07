"""The privacy filter.

Every finding passes through here twice:

1. **before persistence**, so a credential or an address never reaches the
   database in the first place — a database dump cannot leak what was never
   written;
2. **before export**, so a report or API response can apply a stricter policy
   than storage did (for example, suppressing PERSONAL content in a report
   intended for wide circulation).

The filter is monotonic: it can only raise a classification and only remove
content. It never reveals something a collector marked as sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.enums import Classification
from app.privacy.classifier import (
    ClassificationResult,
    at_least,
    classify_value,
    exceeds,
    max_classification,
)
from app.privacy.secrets import REDACTION, redact_text, scan_text

log = get_logger(__name__)

#: Replacement markers, distinct so a reader can tell *why* something is gone.
REDACTED_VALUE = "[REDACTED]"
SUPPRESSED_VALUE = "[SUPPRESSED: SENSITIVE PERSONAL DATA]"

#: Every marker this module can substitute for a real value, including the
#: secret-scanner's. Grouped so callers can ask one question instead of
#: remembering three constants.
REDACTION_MARKERS: frozenset[str] = frozenset({REDACTED_VALUE, SUPPRESSED_VALUE, REDACTION})


def is_redacted(value: object) -> bool:
    """True when ``value`` is a redaction marker rather than real content.

    Downstream code must ask this before handing a field to a typed parser.
    The markers are bracketed, and a bracketed string is IPv6-literal syntax to
    :func:`urllib.parse.urlsplit` — so ``urlsplit("https://[REDACTED]")`` parses
    ``REDACTED`` as an IP address and raises. The value is not a malformed URL;
    it is not a URL at all, and treating it as one turns a privacy decision into
    a parse error that silently drops the whole finding.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text in REDACTION_MARKERS or any(marker in text for marker in REDACTION_MARKERS)


#: Keys whose values are structural and must survive filtering, because
#: removing them would break provenance rather than protect anybody.
PRESERVED_KEYS = frozenset(
    {
        "url",
        "source_url",
        "profile_url",
        "html_url",
        "archived_url",
        "original_url",
        "hostname",
        "domain",
        "record_type",
        "platform",
        "repository",
        "secret_type",
        "status",
        "commit",
    }
)

MAX_STRING_LENGTH = 4000


@dataclass(slots=True)
class FilterOutcome:
    """The result of filtering one finding's payload."""

    data: dict[str, Any]
    classification: Classification
    redacted: bool = False
    reasons: list[str] = field(default_factory=list)
    #: Field paths that were changed, so the UI can show *what* was withheld.
    redacted_fields: list[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


class PrivacyFilter:
    """Classifies and redacts finding payloads."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------ storage

    def filter_finding(
        self,
        data: dict[str, Any],
        *,
        declared: Classification = Classification.PUBLIC,
        redact_from: Classification = Classification.SENSITIVE,
    ) -> FilterOutcome:
        """Filter a payload for storage.

        Args:
            declared: the collector's own classification. Only ever raised.
            redact_from: the level at or above which values are replaced.
        """
        outcome = FilterOutcome(data={}, classification=declared)
        outcome.data = self._walk(data, "", outcome, redact_from)
        if not self.settings.privacy_redaction_enabled and outcome.redacted:
            # Redaction is a deliberate setting; if it is off, say so loudly
            # rather than pretending the data was clean.
            outcome.add_reason(
                "PRIVACY_REDACTION_ENABLED is false: values were classified but not removed"
            )
        return outcome

    def _walk(
        self,
        value: Any,
        path: str,
        outcome: FilterOutcome,
        redact_from: Classification,
        depth: int = 0,
    ) -> Any:
        if depth > 12:
            return "[TRUNCATED: structure too deep]"

        if isinstance(value, dict):
            return {
                key: self._walk(item, _join(path, str(key)), outcome, redact_from, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [
                self._walk(item, f"{path}[{index}]", outcome, redact_from, depth + 1)
                for index, item in enumerate(value)
            ]
        return self._scalar(value, path, outcome, redact_from)

    def _scalar(
        self, value: Any, path: str, outcome: FilterOutcome, redact_from: Classification
    ) -> Any:
        key = path.rsplit(".", 1)[-1].split("[")[0]
        assessment: ClassificationResult = classify_value(key, value)
        outcome.classification = max_classification(outcome.classification, assessment.level)
        for reason in assessment.reasons:
            outcome.add_reason(reason)

        if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
            value = value[:MAX_STRING_LENGTH] + "…[truncated]"

        # A credential-shaped string is removed wherever it appears, whatever
        # the field is called, and even if the field itself looks harmless.
        if isinstance(value, str) and value not in {REDACTION, REDACTED_VALUE}:
            matches = scan_text(value)
            if matches:
                outcome.classification = max_classification(
                    outcome.classification, Classification.RESTRICTED
                )
                outcome.redacted = True
                outcome.redacted_fields.append(path)
                outcome.add_reason(
                    f"A value matching the format of a {matches[0].category} was removed"
                )
                if not self.settings.privacy_redaction_enabled:
                    return value
                cleaned, _ = redact_text(value)
                return cleaned

        if assessment.redact and at_least(assessment.level, redact_from):
            if key in PRESERVED_KEYS and assessment.level is not Classification.RESTRICTED:
                return value
            outcome.redacted = True
            outcome.redacted_fields.append(path)
            if not self.settings.privacy_redaction_enabled:
                return value
            return (
                REDACTED_VALUE
                if assessment.level is Classification.RESTRICTED
                else SUPPRESSED_VALUE
            )
        return value

    # ------------------------------------------------------------- export

    def filter_for_export(
        self,
        data: dict[str, Any],
        *,
        classification: Classification,
        max_level: Classification = Classification.PERSONAL,
    ) -> tuple[dict[str, Any], bool]:
        """Apply an export policy stricter than the storage policy.

        Content classified above ``max_level`` is replaced wholesale, so a
        report can be circulated more widely than the case database. Returns
        ``(payload, withheld)``.
        """
        if not exceeds(classification, max_level):
            return data, False
        return (
            {
                "withheld": True,
                "classification": str(classification),
                "reason": (
                    f"Content classified {classification} exceeds the export policy "
                    f"({max_level}) and was withheld."
                ),
            },
            True,
        )

    def excerpt(self, text: str, limit: int = 500) -> str:
        """A short, credential-free excerpt safe to render in a report."""
        cleaned, _ = redact_text(text or "")
        cleaned = cleaned.strip()
        return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


#: Shared instance for callers that do not need custom settings.
default_filter = PrivacyFilter()
