"""SSRF guard behaviour."""

from __future__ import annotations

import pytest

from app.core.errors import SSRFError
from app.core.ssrf import is_safe_url, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.4.4/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_blocks_internal_targets(url):
    with pytest.raises(SSRFError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "",
        "https://",
        "http://user:pass@example.com/",
    ],
)
def test_blocks_bad_schemes_and_shapes(url):
    with pytest.raises(SSRFError):
        validate_url(url)


def test_blocks_ipv4_mapped_loopback():
    with pytest.raises(SSRFError):
        validate_url("http://[::ffff:127.0.0.1]/")


def test_allows_public_literal_without_dns():
    target = validate_url("https://93.184.215.14/")
    assert target.hostname == "93.184.215.14"
    assert target.port == 443


def test_resolution_can_be_skipped():
    target = validate_url("https://example.com/path", resolve=False)
    assert target.hostname == "example.com"
    assert target.addresses == ()


def test_rejects_overlong_url():
    assert not is_safe_url("https://example.com/" + "a" * 4000, resolve=False)


def test_private_networks_allowed_when_configured(monkeypatch):
    from app.core import settings as settings_module

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORKS", "true")
    settings_module.reset_settings_cache()
    try:
        target = validate_url("http://10.1.2.3/internal")
        assert target.hostname == "10.1.2.3"
        # The metadata endpoint stays blocked even in authorised internal mode.
        with pytest.raises(SSRFError):
            validate_url("http://169.254.169.254/")
    finally:
        settings_module.reset_settings_cache()
