"""Google Web Search Service: configured, adapter-ready, and inert.

Three things this file exists to prove, because each of them is the kind of thing
that rots quietly:

* selecting the provider does not make it pretend to work;
* configuring it does not require credentials unless it is selected, and
  selecting it still does not activate it;
* there is no path from this provider to scraping Google's HTML.
"""

from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError
from app.core.settings import Settings
from app.services.providers.google_wss import (
    PENDING,
    GoogleWebSearchServiceProvider,
    normalise,
)
from app.services.providers.search import (
    OUTCOME_UNAVAILABLE,
    PlannedQuery,
    SearchBrief,
    get_search_provider,
    provider_class,
)

pytestmark = pytest.mark.anyio


def _settings(**overrides) -> Settings:
    base = {"_env_file": None}
    base.update(overrides)
    return Settings(**base)


def test_the_provider_is_registered_and_selectable():
    assert provider_class("google_wss") is GoogleWebSearchServiceProvider
    provider = get_search_provider(_settings(search_provider="google_wss"))
    assert isinstance(provider, GoogleWebSearchServiceProvider)


def test_configuring_it_needs_no_credentials_unless_it_is_selected():
    """The zero-cost default must not acquire a new requirement.

    A deployment that never sets GOOGLE_WSS_* and never selects the provider is
    unaffected: settings load, the default provider stays ``none``, and nothing
    reads a Google credential.
    """
    settings = _settings()
    assert settings.search_provider == "none"
    assert settings.google_wss_api_key is None
    assert settings.google_wss_client_id is None
    assert get_search_provider(settings).key == "none"


def test_it_is_never_available_even_with_credentials_set():
    """A key is not access. Every blocker is outside this repository."""
    provider = GoogleWebSearchServiceProvider(
        _settings(
            search_provider="google_wss",
            google_wss_api_key="k",
            google_wss_client_id="c",
            google_wss_endpoint="https://example.org/search",
        )
    )
    available, reason = provider.is_available()
    assert available is False
    assert PENDING in reason
    assert "credentials" in reason


def test_the_activation_state_is_declared_on_the_class():
    assert GoogleWebSearchServiceProvider.activation_state == "PENDING_PARTNER_ACCESS"


def test_the_configuration_state_reports_presence_and_never_values():
    provider = GoogleWebSearchServiceProvider(
        _settings(google_wss_api_key="super-secret-key", google_wss_client_id="client-123")
    )
    state = provider.configuration_state()
    assert state["api_key_configured"] is True
    assert state["client_id_configured"] is True
    assert state["activation_state"] == PENDING
    assert state["blockers"]
    blob = str(state)
    assert "super-secret-key" not in blob
    assert "client-123" not in blob


async def test_a_single_query_raises_rather_than_attempting_a_request(mock_http):
    """No respx route is registered: a request would fail the hermetic suite."""
    provider = GoogleWebSearchServiceProvider(_settings(google_wss_api_key="k"))
    with pytest.raises(ConfigurationError):
        await provider.search("anything")


async def test_discover_reports_the_channel_unavailable_and_makes_no_request(mock_http):
    provider = GoogleWebSearchServiceProvider(_settings(google_wss_api_key="k"))
    batch = await provider.discover(
        SearchBrief(subject="Example Person", planned=(PlannedQuery(query='"Example Person"'),))
    )
    assert batch.outcome == OUTCOME_UNAVAILABLE
    assert batch.results == []
    assert batch.executed_queries == []
    assert PENDING in batch.reason


def test_the_result_mapping_is_written_and_keeps_absent_fields_absent():
    """One function to change when the schema is documented, tested now."""
    result = normalise(
        {
            "title": "Example Person — Example University",
            "url": "https://example.org/people/example-person",
            "snippet": "Lecturer at Example University",
            "displayLink": "example.org",
        },
        position=1,
        query='"Example Person"',
    )
    assert result is not None
    assert result.provider == "google_wss"
    assert result.snippet == "Lecturer at Example University"
    assert result.provider_position == 1
    assert result.position_is_rank is True
    assert result.query == '"Example Person"'


def test_a_result_without_a_description_is_none_not_an_empty_string():
    result = normalise({"title": "Example", "url": "https://example.org/a"}, position=2, query="q")
    assert result is not None
    assert result.snippet is None


def test_a_result_without_a_url_is_dropped():
    assert normalise({"title": "Example"}, position=1, query="q") is None


def test_there_is_no_google_html_scraping_anywhere_in_the_provider():
    """A guard against the easy wrong answer if credentials never arrive."""
    import pathlib

    source = pathlib.Path("app/services/providers/google_wss.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("google.com/search", "selectolax", "beautifulsoup", "html.parser"):
        assert forbidden not in lowered
