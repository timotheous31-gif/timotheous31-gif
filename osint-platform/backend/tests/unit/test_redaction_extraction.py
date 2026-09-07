"""Redacted values must never reach a typed parser.

The bug this pins: the privacy filter substitutes bracketed markers
(``[REDACTED]``), and a bracketed netloc is IPv6-literal syntax to
``urllib.parse.urlsplit`` — so ``urlsplit("https://[REDACTED]")`` handed
``REDACTED`` to :mod:`ipaddress` and raised

    ValueError: 'REDACTED' does not appear to be an IPv4 or IPv6 address

which extraction logged as ``finding_skipped`` and dropped the whole finding.
A privacy decision presented itself as malformed data.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app.correlation.extraction import RedactedValueError, _website_entity, extract
from app.models.enums import FindingKind
from app.privacy import REDACTED_VALUE, SUPPRESSED_VALUE, is_redacted


def _candidate(**overrides):
    data = {
        "url": "https://orcid.org/0000-0002-1825-0097",
        "host": "orcid.org",
        "source": "orcid",
        "subject_value": "example person",
        "subject_name": "Example Person",
        "name": "Example Person",
        "identifiers": {},
        "affiliations": [],
        "locations": [],
        "handles": [],
        "match_reasons": [],
        "mismatch_reasons": [],
        "corroborated_by": [],
    }
    data.update(overrides)
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=FindingKind.PERSON_CANDIDATE,
        collector="orcid",
        source_url=data.get("url"),
        data=data,
        title="Example",
        observed_at=None,
    )


def test_the_marker_really_does_break_urlsplit():
    """The precondition. If this ever stops raising, the guard can be dropped."""
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 address"):
        urlsplit(f"https://{REDACTED_VALUE}")


@pytest.mark.parametrize("marker", [REDACTED_VALUE, SUPPRESSED_VALUE])
def test_a_redacted_url_is_refused_before_it_is_parsed(marker):
    with pytest.raises(RedactedValueError) as excinfo:
        _website_entity(marker, _candidate(), "search_reference")
    assert excinfo.value.field == "url"
    # Not an IP-parsing complaint: the message must say what actually happened.
    assert "redacted" in str(excinfo.value).lower()
    assert "IPv4" not in str(excinfo.value)


@pytest.mark.parametrize("marker", [REDACTED_VALUE, SUPPRESSED_VALUE])
def test_extraction_survives_a_redacted_finding(marker):
    """The rest of the batch must still extract, and nothing may crash."""
    good = _candidate(url="https://openalex.org/A123", source="openalex", host="openalex.org")
    result = extract([_candidate(url=marker), good])
    values = {entity.canonical_value for entity in result.entities.values()}
    assert "person-candidate:https://openalex.org/A123" in values
    assert not any(marker in value for value in values)


def test_a_redacted_marker_never_becomes_an_entity_identity():
    result = extract([_candidate(url=REDACTED_VALUE)])
    assert all("REDACTED" not in entity.canonical_value for entity in result.entities.values())


def test_is_redacted_recognises_markers_without_flagging_real_values():
    assert is_redacted(REDACTED_VALUE)
    assert is_redacted(SUPPRESSED_VALUE)
    assert is_redacted(f"https://{REDACTED_VALUE}")
    assert not is_redacted("https://orcid.org/0000-0002-1825-0097")
    assert not is_redacted("")
    assert not is_redacted(None)
