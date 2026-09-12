"""Credential handling and the configuration each collector reports.

The regression these guard against was found in a real Docker run:

    github 1.0.0 — FAILED — Illegal header value b'Bearer '

``docker-compose.yml`` forwards optional keys as ``GITHUB_TOKEN: ${GITHUB_TOKEN:-}``,
so an unset key arrives as an empty string rather than not arriving. That parsed
to ``SecretStr('')``, every ``is None`` guard read it as *configured*, and the
collector built an ``Authorization: Bearer `` header that httpx rejects before
the request leaves the process — turning "no token" into a hard failure instead
of the supported anonymous mode.
"""

from __future__ import annotations

import pytest

from app.collectors.email import EmailCollector
from app.collectors.github import GitHubCollector
from app.collectors.search import SearchCollector
from app.core.settings import Settings
from app.models.enums import TargetType

#: Not a credential — a syntactically plausible placeholder for header assertions.
EXAMPLE_TOKEN = "ghp_" + "0" * 36


#: Every credential this module reasons about, pinned absent by default.
_CREDENTIALS = (
    "github_token",
    "hibp_api_key",
    "brave_api_key",
    "bing_api_key",
    "serper_api_key",
)


def settings(**overrides) -> Settings:
    """Settings with no credentials unless the test supplies one.

    Both the ``.env`` file and the ambient environment are excluded: a developer
    (or a CI runner) who happens to export ``GITHUB_TOKEN`` must not turn an
    assertion about the *absent* case into a false pass or a false failure.
    """
    absent: dict[str, object] = dict.fromkeys(_CREDENTIALS)
    return Settings(_env_file=None, **{**absent, **overrides})


# ------------------------------------------------------- blank credentials


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n"])
@pytest.mark.parametrize(
    "field",
    ["github_token", "hibp_api_key", "brave_api_key", "bing_api_key", "serper_api_key"],
)
def test_a_blank_credential_is_read_as_absent(field, blank):
    """Every credential, not just the one that happened to crash."""
    resolved = getattr(settings(**{field: blank}), field)
    assert resolved is None, f"{field}={blank!r} should be absent, got {resolved!r}"


@pytest.mark.parametrize("blank", ["", "   "])
def test_github_sends_no_authorization_header_for_a_blank_token(blank):
    collector = GitHubCollector(settings(github_token=blank))
    headers = collector._headers()
    assert "Authorization" not in headers
    # The precise failure mode: a header value ending in a space is illegal.
    assert not any(value != value.strip() for value in headers.values())


def test_github_sends_no_authorization_header_without_a_token():
    collector = GitHubCollector(settings())
    assert "Authorization" not in collector._headers()


def test_github_sends_a_bearer_header_with_a_configured_token():
    collector = GitHubCollector(settings(github_token=EXAMPLE_TOKEN))
    assert collector._headers()["Authorization"] == f"Bearer {EXAMPLE_TOKEN}"


def test_github_strips_whitespace_around_a_configured_token():
    """A key pasted with a trailing newline must not produce an illegal header."""
    collector = GitHubCollector(settings(github_token=f"  {EXAMPLE_TOKEN}\n"))
    assert collector._headers()["Authorization"] == f"Bearer {EXAMPLE_TOKEN}"


@pytest.mark.parametrize("token", ["", "   ", None])
def test_github_is_still_available_without_a_token(token):
    """Anonymous access is a supported mode, so this is not an outage."""
    collector = GitHubCollector(settings(github_token=token) if token is not None else settings())
    available, reason = collector.is_available()
    assert available is True
    assert reason == ""


def test_hibp_treats_a_blank_key_as_absent():
    """The same latent bug lived here; it would have sent an empty API key."""
    collector = EmailCollector(settings(hibp_api_key=""))
    assert collector.settings.hibp_api_key is None


# ----------------------------------------------- reported configuration state


def test_github_reports_its_authentication_mode_without_the_token():
    configured = GitHubCollector(settings(github_token=EXAMPLE_TOKEN)).configuration()
    anonymous = GitHubCollector(settings(github_token="")).configuration()

    assert configured.mode == "authenticated"
    assert anonymous.mode == "unauthenticated"
    assert configured.optional_settings == ["GITHUB_TOKEN"]
    # The value must never reach the API surface, in any field.
    assert EXAMPLE_TOKEN not in str(configured.as_dict())
    assert "60 requests/hour" in anonymous.detail


def test_search_names_the_provider_and_key_it_needs():
    unconfigured = SearchCollector(settings()).configuration()
    assert unconfigured.configured is False
    assert unconfigured.required_settings == ["SEARCH_PROVIDER"]
    assert unconfigured.mode == "none"
    assert "brave, bing or serper" in unconfigured.detail


@pytest.mark.parametrize(
    ("provider", "key_field", "expected_setting"),
    [
        ("brave", "brave_api_key", "BRAVE_API_KEY"),
        ("bing", "bing_api_key", "BING_API_KEY"),
        ("serper", "serper_api_key", "SERPER_API_KEY"),
    ],
)
def test_search_names_the_missing_key_for_each_provider(provider, key_field, expected_setting):
    """Selecting a provider without its key must say *which* key is missing."""
    config = SearchCollector(settings(search_provider=provider)).configuration()
    assert config.configured is False
    assert config.required_settings == ["SEARCH_PROVIDER", expected_setting]
    assert expected_setting in config.detail


@pytest.mark.parametrize(
    ("provider", "key_field"),
    [("brave", "brave_api_key"), ("bing", "bing_api_key"), ("serper", "serper_api_key")],
)
def test_search_is_operational_once_configured(provider, key_field):
    collector = SearchCollector(settings(search_provider=provider, **{key_field: "example-key"}))
    config = collector.configuration()
    assert config.configured is True
    assert config.mode == provider
    assert "Operational" in config.detail
    assert collector.is_available()[0] is True
    assert "example-key" not in str(config.as_dict())


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_search_key_is_not_configured(blank):
    """Setting the provider but leaving its key empty must not read as ready."""
    collector = SearchCollector(settings(search_provider="brave", brave_api_key=blank))
    assert collector.is_available()[0] is False
    assert collector.configuration().configured is False


def test_search_skip_reason_is_preserved_verbatim():
    """The runtime message the operator already knows must not drift."""
    available, reason = SearchCollector(settings()).is_available()
    assert available is False
    assert reason == (
        "No search provider is configured. Set SEARCH_PROVIDER to "
        "anthropic_web_search, brave, bing or serper and supply the matching "
        "credential. Every free structured source and the manual search "
        "workflow keep working with no provider at all."
    )


def test_search_accepts_person_targets():
    assert SearchCollector.accepts(TargetType.PERSON)


def test_collector_metadata_exposes_configuration_and_no_secrets():
    from app.collectors.registry import collector_metadata, load_builtin_collectors

    load_builtin_collectors()
    entries = collector_metadata()
    assert entries, "no collectors registered"
    for entry in entries:
        config = entry["configuration"]
        assert set(config) == {
            "required_settings",
            "optional_settings",
            "configured",
            "mode",
            "detail",
        }
        # Setting *names* only: nothing that could carry a value.
        for name in config["required_settings"] + config["optional_settings"]:
            assert name.isupper(), f"{name} does not look like a setting name"
