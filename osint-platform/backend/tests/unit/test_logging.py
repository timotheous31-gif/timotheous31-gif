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


def test_logging_survives_a_replaced_stream():
    """Configuration must not capture whatever stderr happened to be.

    pytest, Typer's CliRunner and daemonised workers all swap sys.stderr;
    binding it at configure time leaves later logging writing to a closed file.
    """
    import io
    import sys

    from app.core.logging import configure_logging, get_logger

    original = sys.stderr
    captured = io.StringIO()
    sys.stderr = captured
    try:
        configure_logging(level="INFO")
        get_logger("test").info("first.event")
        assert "first.event" in captured.getvalue()
    finally:
        sys.stderr = original
    captured.close()

    # The stream that was live at configure time is now closed; logging must
    # still work against the current stderr rather than raising.
    get_logger("test").info("second.event")


def test_write_to_a_closed_stream_is_swallowed():
    import io
    import sys

    from app.core.logging import _StderrProxy

    original = sys.stderr
    closed = io.StringIO()
    closed.close()
    sys.stderr = closed
    try:
        assert _StderrProxy().write("anything") == 0
        _StderrProxy().flush()
    finally:
        sys.stderr = original
