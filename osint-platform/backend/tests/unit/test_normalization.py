"""Target normalisation and type detection."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models.enums import TargetType
from app.services.normalization import (
    detect_type,
    normalize_target,
    registrable_domain,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@alice", TargetType.USERNAME),
        ("alice_99", TargetType.USERNAME),
        ("example.com", TargetType.DOMAIN),
        ("sub.example.co.uk", TargetType.DOMAIN),
        ("user@example.com", TargetType.EMAIL),
        ("https://example.com/page", TargetType.URL),
        ("203.0.113.7", TargetType.IP),
        ("2001:db8::1", TargetType.IP),
        ("octocat/Hello-World", TargetType.REPOSITORY),
        ("https://github.com/octocat/Hello-World", TargetType.REPOSITORY),
        ("git@github.com:octocat/Hello-World.git", TargetType.REPOSITORY),
        ("https://reddit.com/u/exampleuser", TargetType.SOCIAL_PROFILE),
        ("Example Corporation", TargetType.ORGANIZATION),
    ],
)
def test_detects_target_type(raw, expected):
    assert detect_type(raw) is expected


def test_username_is_lowercased_and_stripped():
    target = normalize_target("  @ExampleUser ")
    assert target.type is TargetType.USERNAME
    assert target.value == "exampleuser"
    assert target.attributes["case_sensitive"] == "ExampleUser"


def test_domain_is_lowercased_and_trailing_dot_removed():
    target = normalize_target("Example.COM.", TargetType.DOMAIN)
    assert target.value == "example.com"
    assert target.attributes["labels"] == ["example", "com"]


def test_domain_accepts_unicode_via_idna():
    target = normalize_target("bücher.de", TargetType.DOMAIN)
    assert target.value == "xn--bcher-kva.de"
    assert target.attributes["unicode"] == "bücher.de"


def test_domain_extracted_from_pasted_url():
    assert normalize_target("https://www.example.com/a/b", TargetType.DOMAIN).value == (
        "www.example.com"
    )


def test_email_is_lowercased_and_split():
    target = normalize_target("Alice.Example@Example.ORG")
    assert target.value == "alice.example@example.org"
    assert target.attributes["domain"] == "example.org"
    assert target.attributes["local_part"] == "alice.example"


def test_url_drops_fragment_and_default_port():
    target = normalize_target("HTTPS://Example.com:443/Path?b=2#section")
    assert target.value == "https://example.com/Path?b=2"
    assert target.attributes["scheme"] == "https"


def test_url_keeps_non_default_port():
    assert normalize_target("http://example.com:8080/x").value == "http://example.com:8080/x"


def test_url_without_path_gets_root():
    assert normalize_target("https://example.com", TargetType.URL).value == "https://example.com/"


def test_ip_normalisation_records_scope():
    target = normalize_target("2001:0db8:0000::1")
    assert target.value == "2001:db8::1"
    assert target.attributes["version"] == 6
    # 2001:db8::/32 is the documentation range, which ipaddress reports private.
    assert target.attributes["is_private"] is True
    assert normalize_target("93.184.215.14").attributes["is_global"] is True


def test_repository_forms_agree():
    a = normalize_target("octocat/Hello-World")
    b = normalize_target("https://github.com/octocat/Hello-World")
    c = normalize_target("git@github.com:octocat/Hello-World.git")
    assert a.value == b.value == c.value == "github:octocat/hello-world"
    assert b.attributes["url"] == "https://github.com/octocat/Hello-World"


def test_gitlab_repository_keeps_its_platform():
    assert normalize_target("https://gitlab.com/group/project").value == "gitlab:group/project"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://reddit.com/u/exampleuser", "reddit:exampleuser"),
        ("https://reddit.com/user/exampleuser", "reddit:exampleuser"),
        ("https://mastodon.social/@exampleuser", "mastodon:exampleuser"),
        ("https://news.ycombinator.com/user?id=exampleuser", "hackernews:exampleuser"),
    ],
)
def test_social_profile_handles(raw, expected):
    assert normalize_target(raw).value == expected


def test_organization_canonicalisation_ignores_suffixes():
    a = normalize_target("Example Corporation", TargetType.ORGANIZATION)
    b = normalize_target("  example   corp.  ", TargetType.ORGANIZATION)
    assert a.value == b.value == "example"
    assert a.attributes["display_name"] == "Example Corporation"


@pytest.mark.parametrize(
    ("raw", "target_type"),
    [
        ("", None),
        ("   ", None),
        ("a" * 1100, None),
        ("not a domain", TargetType.DOMAIN),
        ("@@@", TargetType.USERNAME),
        ("no-at-sign", TargetType.EMAIL),
        ("ftp://example.com", TargetType.URL),
        ("999.999.999.999", TargetType.IP),
        ("just-one-part", TargetType.REPOSITORY),
        ("x", TargetType.ORGANIZATION),
    ],
)
def test_invalid_inputs_raise(raw, target_type):
    with pytest.raises(ValidationError):
        normalize_target(raw, target_type)


def test_none_input_raises():
    with pytest.raises(ValidationError):
        normalize_target(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("example.co.uk", "example.co.uk"),
        ("localhost", "localhost"),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected
