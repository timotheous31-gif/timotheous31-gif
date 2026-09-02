"""Detection of accidentally published credentials.

This module answers one question — *does this string look like a credential?* —
and deliberately refuses to answer any other. It never returns, stores, logs or
transmits the matched value. Callers receive a category, a position and a
confidence, which is enough to tell a repository owner "you have an AWS key in
your README" without the platform itself becoming a place credentials live.

The detector is also used defensively, to redact anything credential-shaped
that a collector happens to pull back from a public source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

REDACTION: Final = "[REDACTED SECRET]"


@dataclass(frozen=True, slots=True)
class SecretMatch:
    """A credential-shaped string. The value itself is never carried here."""

    category: str
    #: Character offsets in the scanned text, so a caller can redact in place.
    start: int
    end: int
    confidence: float
    #: A non-reversible hint: length and the first character class only.
    shape: str

    def masked(self) -> str:
        return REDACTION


@dataclass(frozen=True, slots=True)
class SecretPattern:
    name: str
    pattern: re.Pattern[str]
    confidence: float


#: Patterns for credentials whose formats are publicly documented by their
#: issuers. Ordered most-specific first so a token is reported once.
PATTERNS: Final[tuple[SecretPattern, ...]] = (
    SecretPattern("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"), 0.98),
    SecretPattern(
        "AWS access key id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), 0.95
    ),
    SecretPattern("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), 0.95),
    SecretPattern(
        "GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), 0.95
    ),
    SecretPattern("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,250}\b"), 0.95),
    SecretPattern("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,99}\b"), 0.95),
    SecretPattern("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0.9),
    SecretPattern("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), 0.85),
    SecretPattern(
        "SendGrid API key",
        re.compile(r"\bSG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\b"),
        0.95,
    ),
    SecretPattern("Twilio account SID", re.compile(r"\bAC[0-9a-fA-F]{32}\b"), 0.85),
    SecretPattern(
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
        0.85,
    ),
    SecretPattern(
        "Basic authorization header",
        re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/]{16,}={0,2}"),
        0.8,
    ),
    SecretPattern("Bearer token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}"), 0.75),
    SecretPattern(
        "URL with embedded credentials",
        re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]+@"),
        0.9,
    ),
    SecretPattern(
        "generic API key assignment",
        re.compile(
            r"(?i)\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token|"
            r"client[_\-]?secret|password|passwd)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+_\-]{16,}[\"']?"
        ),
        0.6,
    ),
)

#: Obvious placeholders. Flagging these would only create noise.
_PLACEHOLDERS: Final = re.compile(
    r"(?i)(example|sample|placeholder|your[_\-]?|dummy|test[_\-]?key|xxxx+|<[^>]+>|"
    r"changeme|redacted|\bfake\b|000000000000|aaaaaaaaaaaa)"
)


def scan_text(text: str, *, max_length: int = 2_000_000) -> list[SecretMatch]:
    """Return the credential-shaped spans in ``text``.

    The matched substrings are not included in the result — only their
    category, position, confidence and a coarse shape hint.
    """
    if not text:
        return []
    sample = text[:max_length]
    matches: list[SecretMatch] = []
    claimed: list[tuple[int, int]] = []

    for spec in PATTERNS:
        for found in spec.pattern.finditer(sample):
            start, end = found.span()
            if any(start < c_end and end > c_start for c_start, c_end in claimed):
                continue
            value = found.group(0)
            if _PLACEHOLDERS.search(value):
                continue
            claimed.append((start, end))
            matches.append(
                SecretMatch(
                    category=spec.name,
                    start=start,
                    end=end,
                    confidence=spec.confidence,
                    shape=_shape(value),
                )
            )
    matches.sort(key=lambda match: match.start)
    return matches


def redact_text(text: str) -> tuple[str, list[SecretMatch]]:
    """Replace every credential-shaped span in ``text`` with the redaction marker."""
    matches = scan_text(text)
    if not matches:
        return text, []
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(text[cursor : match.start])
        pieces.append(REDACTION)
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces), matches


def contains_secret(text: str) -> bool:
    """True when ``text`` contains anything credential-shaped."""
    return bool(scan_text(text))


def _shape(value: str) -> str:
    """A non-reversible description: length plus the leading character class."""
    if not value:
        return "empty"
    first = value[0]
    kind = "alpha" if first.isalpha() else "digit" if first.isdigit() else "symbol"
    return f"{kind}:{len(value)}"
