"""Classification of finding content by sensitivity.

Four levels, in increasing order of restriction:

``PUBLIC``
    Infrastructure and organisational facts that a registry or the subject
    themselves published: DNS records, certificates, repository metadata.

``PERSONAL``
    Relates to an identifiable person but was self-published: a profile
    biography, a display name, an email address on a public profile. Stored and
    shown, but marked so an investigator knows to handle it with care.

``SENSITIVE``
    Would harm or expose a person if aggregated: precise locations, dates of
    birth, breach exposure, anything that reads as a residential address.
    Stored in reduced form and redacted in exports by default.

``RESTRICTED``
    Must never be stored in full: credentials, tokens, private keys, financial
    account numbers, government identifiers. The value is replaced before it
    reaches the database.

Classification is *monotonic*: a collector's own assessment can only be raised
here, never lowered.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import Classification

#: Ends a key pattern. ``\b`` is wrong for field names because "_" is a word
#: character, so ``\bphone\b`` would not match ``phone_number``.
KEY_END = r"(?![A-Za-z0-9])"

_ORDER = {
    Classification.PUBLIC: 0,
    Classification.PERSONAL: 1,
    Classification.SENSITIVE: 2,
    Classification.RESTRICTED: 3,
}


def max_classification(*values: Classification) -> Classification:
    """Return the most restrictive of ``values``."""
    return max(values, key=lambda value: _ORDER[value]) if values else Classification.PUBLIC


def at_least(value: Classification, minimum: Classification) -> bool:
    """True when ``value`` is at least as restrictive as ``minimum``."""
    return _ORDER[value] >= _ORDER[minimum]


def exceeds(value: Classification, maximum: Classification) -> bool:
    """True when ``value`` is strictly more restrictive than ``maximum``."""
    return _ORDER[value] > _ORDER[maximum]


def luhn_valid(digits: str) -> bool:
    """Luhn checksum, used to keep number-shaped text from being called a card.

    Without it, a pattern loose enough to catch "4111 1111 1111 1111" also
    catches things like a DNS SOA record's serial and timer values.
    """
    stripped = [int(char) for char in digits if char.isdigit()]
    if not 13 <= len(stripped) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(stripped)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class ClassificationRule:
    """One pattern that raises the classification of a value."""

    name: str
    level: Classification
    #: Matched against the *field name*, the *value*, or both.
    key_pattern: re.Pattern[str] | None = None
    value_pattern: re.Pattern[str] | None = None
    #: True when the matched value must be replaced rather than merely marked.
    redact: bool = False
    explanation: str = ""
    #: Optional second check applied to a ``value_pattern`` match, for patterns
    #: that would otherwise be too loose to be trusted on their own.
    value_validator: Callable[[str], bool] | None = None

    def matches(self, key: str, value: str) -> bool:
        if self.key_pattern is not None and self.key_pattern.search(key):
            return True
        if self.value_pattern is None:
            return False
        found = self.value_pattern.search(value)
        if found is None:
            return False
        if self.value_validator is not None:
            return self.value_validator(found.group(0))
        return True


#: Government identifiers, financial data and precise addresses. Patterns are
#: intentionally conservative: a false positive costs a redaction, a false
#: negative costs someone their privacy.
RULES: tuple[ClassificationRule, ...] = (
    ClassificationRule(
        name="credential",
        level=Classification.RESTRICTED,
        key_pattern=re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_\-]?key|private[_\-]?key|"
            r"credential|authorization|cookie|session[_\-]?id|access[_\-]?key)" + KEY_END
        ),
        redact=True,
        explanation="Credential material is never stored by this platform",
    ),
    ClassificationRule(
        name="payment_card",
        level=Classification.RESTRICTED,
        key_pattern=re.compile(
            r"(?i)\b(card[_\-]?number|iban|bic|swift|account[_\-]?number)" + KEY_END
        ),
        # Digit groups in card-like shapes, confirmed by the Luhn checksum.
        value_pattern=re.compile(r"\b(?:\d{4}[ \-]?){3}\d{1,7}\b|\b\d{13,19}\b"),
        value_validator=luhn_valid,
        redact=True,
        explanation="Financial account identifiers are never stored",
    ),
    ClassificationRule(
        name="government_id",
        level=Classification.RESTRICTED,
        key_pattern=re.compile(
            r"(?i)\b(ssn|social[_\-]?security|passport[_\-]?(no|number)|national[_\-]?id|"
            r"tax[_\-]?id|driver[_\-]?licen[cs]e|nino|aadhaar)" + KEY_END
        ),
        value_pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        redact=True,
        explanation="Government identification numbers are never stored",
    ),
    ClassificationRule(
        name="residential_address",
        level=Classification.SENSITIVE,
        key_pattern=re.compile(
            r"(?i)\b(street[_\-]?address|home[_\-]?address|postal[_\-]?address|"
            r"address[_\-]?line|residence|post[_\-]?code|zip[_\-]?code)" + KEY_END
        ),
        redact=True,
        explanation="Precise residential addresses are suppressed",
    ),
    ClassificationRule(
        name="precise_location",
        level=Classification.SENSITIVE,
        key_pattern=re.compile(
            r"(?i)\b(latitude|longitude|geo[_\-]?coordinates|gps|geolocation)" + KEY_END
        ),
        redact=True,
        explanation="Precise coordinates are suppressed; they enable physical tracking",
    ),
    ClassificationRule(
        name="date_of_birth",
        level=Classification.SENSITIVE,
        key_pattern=re.compile(
            r"(?i)\b(date[_\-]?of[_\-]?birth|dob|birth[_\-]?date|birthday)" + KEY_END
        ),
        redact=True,
        explanation="Dates of birth are suppressed",
    ),
    ClassificationRule(
        name="phone_number",
        level=Classification.SENSITIVE,
        key_pattern=re.compile(r"(?i)\b(phone|mobile|telephone|msisdn|whatsapp)" + KEY_END),
        redact=True,
        explanation="Personal telephone numbers are suppressed",
    ),
    ClassificationRule(
        name="breach_exposure",
        level=Classification.SENSITIVE,
        key_pattern=re.compile(r"(?i)\b(breach|pwned|exposure)"),
        explanation="Breach exposure is sensitive; only names and dates are retained",
    ),
    ClassificationRule(
        name="email_address",
        level=Classification.PERSONAL,
        key_pattern=re.compile(r"(?i)\b(email|e[_\-]?mail|mailbox)" + KEY_END),
        value_pattern=re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b"),
        explanation="Email addresses often identify a person",
    ),
    ClassificationRule(
        name="person_name",
        level=Classification.PERSONAL,
        key_pattern=re.compile(
            r"(?i)\b(full[_\-]?name|first[_\-]?name|last[_\-]?name|surname|given[_\-]?name|"
            r"real[_\-]?name|display[_\-]?name|bio|biography)" + KEY_END
        ),
        explanation="Personal names and biographies relate to an identifiable person",
    ),
    ClassificationRule(
        name="coarse_location",
        level=Classification.PERSONAL,
        key_pattern=re.compile(r"(?i)\b(location|city|country|region|place)" + KEY_END),
        explanation="Self-declared location text relates to an identifiable person",
    ),
)


@dataclass(slots=True)
class ClassificationResult:
    """What the classifier decided about one value."""

    level: Classification
    redact: bool = False
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


def classify_value(key: str, value: Any) -> ClassificationResult:
    """Classify one ``key``/``value`` pair."""
    text = "" if value is None else str(value)
    level = Classification.PUBLIC
    redact = False
    reasons: list[str] = []
    rules: list[str] = []

    for rule in RULES:
        if not rule.matches(key, text):
            continue
        level = max_classification(level, rule.level)
        rules.append(rule.name)
        if rule.explanation and rule.explanation not in reasons:
            reasons.append(rule.explanation)
        if rule.redact:
            redact = True

    return ClassificationResult(level=level, redact=redact, reasons=reasons, rules=rules)
