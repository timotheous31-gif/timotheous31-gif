"""Secret scrubbing in the logging pipeline."""

from __future__ import annotations

from app.core.logging import scrub_secrets


def test_scrubs_secret_shaped_keys():
    event = scrub_secrets(None, "info", {"password": "hunter2", "user": "alice"})
    assert event["password"] == "[REDACTED]"
    assert event["user"] == "alice"


def test_scrubs_nested_keys():
    event = scrub_secrets(None, "info", {"ctx": {"api_key": "abc123", "host": "example.com"}})
    assert event["ctx"]["api_key"] == "[REDACTED]"
    assert event["ctx"]["host"] == "example.com"


def test_scrubs_token_shaped_values():
    event = scrub_secrets(None, "info", {"body": "found ghp_0123456789abcdefghij in a file"})
    assert "ghp_0123456789abcdefghij" not in event["body"]


def test_scrubs_aws_key_and_private_key_header():
    event = scrub_secrets(
        None,
        "info",
        {"a": "AKIAIOSFODNN7EXAMPLE", "b": "-----BEGIN RSA PRIVATE KEY-----"},
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in event["a"]
    assert "PRIVATE KEY" not in event["b"]


def test_scrubs_inside_lists():
    event = scrub_secrets(None, "info", {"items": ["Bearer abcdefghijklmnop", "safe"]})
    assert "abcdefghijklmnop" not in event["items"][0]
    assert event["items"][1] == "safe"
