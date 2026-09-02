"""Secret detection: recognise credentials, never retain them."""

from __future__ import annotations

import pytest

from app.privacy.secrets import REDACTION, contains_secret, redact_text, scan_text


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("AKIA1234567890ABCDEF", "AWS access key id"),
        ("ghp_" + "b" * 36, "GitHub token"),
        ("github_pat_" + "c" * 30, "GitHub fine-grained token"),
        ("xoxb-123456789012-abcdefghijkl", "Slack token"),
        ("sk_live_" + "d" * 24, "Stripe key"),
        ("AIza" + "E" * 35, "Google API key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
        ("SG." + "f" * 22 + "." + "g" * 22, "SendGrid API key"),
        ("https://admin:s3cretpassword@internal.test/", "URL with embedded credentials"),
        ('api_key = "9f8e7d6c5b4a39281706"', "generic API key assignment"),
    ],
)
def test_detects_documented_credential_formats(text, category):
    matches = scan_text(text)
    assert matches, f"no match for {category}"
    assert matches[0].category == category


@pytest.mark.parametrize(
    "text",
    [
        "",
        "just some ordinary prose about a domain",
        "AKIAIOSFODNN7EXAMPLE",
        "api_key = 'your_api_key_here'",
        "token: <REPLACE_ME>",
        "password = changeme",
        "https://example.com/path?q=1",
    ],
)
def test_ignores_placeholders_and_ordinary_text(text):
    assert scan_text(text) == []


def test_match_never_carries_the_value():
    match = scan_text("AKIA1234567890ABCDEF")[0]
    fields = str(match.__dict__ if hasattr(match, "__dict__") else match)
    assert "AKIA1234567890ABCDEF" not in fields
    assert match.masked() == REDACTION
    assert match.shape == "alpha:20"


def test_redaction_replaces_every_span():
    text = f"first ghp_{'a1' * 18} then AKIA1234567890ABCDEF end"
    redacted, matches = redact_text(text)
    assert len(matches) == 2
    assert "ghp_" not in redacted
    assert "AKIA1234567890ABCDEF" not in redacted
    assert redacted.count(REDACTION) == 2
    assert redacted.startswith("first ")
    assert redacted.endswith(" end")


def test_redaction_is_a_no_op_for_clean_text():
    text = "nothing to see here"
    assert redact_text(text) == (text, [])


def test_overlapping_patterns_report_once():
    matches = scan_text("authorization: Bearer " + "z" * 40)
    assert len(matches) == 1


def test_contains_secret_helper():
    assert contains_secret("AKIA1234567890ABCDEF")
    assert not contains_secret("example.com")


def test_offsets_point_at_the_match():
    text = "prefix AKIA1234567890ABCDEF suffix"
    match = scan_text(text)[0]
    assert text[match.start : match.end] == "AKIA1234567890ABCDEF"


def test_scan_is_bounded_for_large_inputs():
    huge = "x" * 100 + "AKIA1234567890ABCDEF"
    assert scan_text(huge, max_length=50) == []
