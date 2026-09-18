"""Search-provider abstraction and the search collector."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.search import SearchCollector
from app.core.errors import ConfigurationError
from app.core.settings import Settings
from app.models.enums import FindingKind, TargetType
from app.services.normalization import normalize_target
from app.services.providers.search import (
    BingSearchProvider,
    BraveSearchProvider,
    NullSearchProvider,
    SearchResult,
    SerperSearchProvider,
    available_providers,
    get_search_provider,
)


def test_all_providers_are_registered():
    assert set(available_providers()) == {
        "none",
        "anthropic_web_search",
        "google_wss",
        "brave",
        "bing",
        "serper",
    }


def test_default_provider_is_none_and_reports_why():
    provider = get_search_provider(Settings(_env_file=None))
    assert isinstance(provider, NullSearchProvider)
    available, reason = provider.is_available()
    assert available is False
    assert "SEARCH_PROVIDER" in reason


async def test_null_provider_raises_rather_than_returning_nothing():
    with pytest.raises(ConfigurationError):
        await NullSearchProvider(Settings(_env_file=None)).search("anything")


def test_unknown_provider_is_rejected_by_settings_validation():
    """An unsupported provider name fails at configuration load, not at run time."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None, search_provider="altavista")


def test_unknown_provider_is_rejected_by_the_registry(monkeypatch):
    """Defence in depth: the lookup also refuses a name it does not know."""
    settings = Settings(_env_file=None)
    monkeypatch.setattr(settings, "search_provider", "altavista", raising=False)
    with pytest.raises(ConfigurationError, match="Unknown search provider"):
        get_search_provider(settings)


@pytest.mark.parametrize(
    ("cls", "setting"),
    [
        (BraveSearchProvider, "brave_api_key"),
        (BingSearchProvider, "bing_api_key"),
        (SerperSearchProvider, "serper_api_key"),
    ],
)
def test_providers_require_their_key(cls, setting):
    without = cls(Settings(_env_file=None))
    assert without.is_available()[0] is False
    assert setting.upper() in without.is_available()[1]

    with_key = cls(Settings(_env_file=None, **{setting: "test-key"}))
    assert with_key.is_available() == (True, "")


@respx.mock
async def test_brave_results_are_normalised(mock_http):
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Example Domain",
                            "url": "https://example.com/",
                            "description": "Reserved for documentation.",
                        },
                        {"title": "No URL"},
                    ]
                }
            },
        )
    )
    provider = BraveSearchProvider(Settings(_env_file=None, brave_api_key="k"))
    results = await provider.search("example")
    assert len(results) == 1
    assert results[0].url == "https://example.com/"
    assert results[0].provider_position == 1
    assert results[0].position_is_rank is True
    assert results[0].host == "example.com"


@respx.mock
async def test_bing_results_are_normalised(mock_http):
    respx.get("https://api.bing.microsoft.com/v7.0/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "webPages": {
                    "value": [{"name": "Example", "url": "https://example.org/", "snippet": "s"}]
                }
            },
        )
    )
    provider = BingSearchProvider(Settings(_env_file=None, bing_api_key="k"))
    results = await provider.search("example")
    assert results[0].title == "Example"
    assert results[0].provider == "bing"


@respx.mock
async def test_serper_results_are_normalised(mock_http):
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Example",
                        "link": "https://example.net/",
                        "snippet": "s",
                        "position": 2,
                    }
                ]
            },
        )
    )
    provider = SerperSearchProvider(Settings(_env_file=None, serper_api_key="k"))
    results = await provider.search("example")
    assert results[0].provider_position == 2
    assert results[0].position_is_rank is True


@respx.mock
async def test_provider_error_is_surfaced(mock_http):
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(401)
    )
    provider = BraveSearchProvider(Settings(_env_file=None, brave_api_key="bad"))
    with pytest.raises(ConfigurationError, match="401"):
        await provider.search("example")


def test_collector_is_unavailable_without_a_provider():
    collector = SearchCollector(Settings(_env_file=None))
    available, reason = collector.is_available()
    assert available is False
    assert "SEARCH_PROVIDER" in reason


@pytest.mark.parametrize(
    ("raw", "explicit_type", "expected_first"),
    [
        ("example.com", None, '"example.com"'),
        # An organisation name is no longer inferable, so the type is stated.
        ("Example Corporation", TargetType.ORGANIZATION, '"Example Corporation"'),
        ("@exampleuser", None, '"exampleuser"'),
        ("user@example.com", None, '"user@example.com"'),
    ],
)
def test_queries_are_narrow_and_quoted(raw, explicit_type, expected_first):
    collector = SearchCollector(Settings(_env_file=None))
    queries = collector.queries(normalize_target(raw, explicit_type))
    assert queries[0] == expected_first
    assert len(queries) <= 2


def test_email_queries_never_add_personal_terms():
    collector = SearchCollector(Settings(_env_file=None))
    queries = collector.queries(normalize_target("user@example.com"))
    joined = " ".join(queries).lower()
    for term in ("address", "phone", "home", "resume", "cv", "family"):
        assert term not in joined


@respx.mock
async def test_collect_deduplicates_urls_across_queries(collector_ctx, mock_http, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    reset_settings_cache()
    try:
        respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"title": "Example", "url": "https://example.com/", "description": "d"}
                        ]
                    }
                },
            )
        )
        collector = SearchCollector()
        collector_ctx.settings = collector.settings
        result = await collector.collect(normalize_target("example.com"), collector_ctx)
        assert result.stats["results"] == 1
        assert len(result.findings) == 1
        assert result.findings[0].kind is FindingKind.SEARCH_RESULT
        assert result.findings[0].confidence <= 0.5
    finally:
        reset_settings_cache()


def test_search_result_host_strips_www():
    result = SearchResult("t", "https://www.example.com/a", "brave", snippet="s")
    assert result.host == "example.com"


def test_collector_supports_the_expected_targets():
    assert SearchCollector.accepts(TargetType.DOMAIN)
    assert SearchCollector.accepts(TargetType.ORGANIZATION)
    assert not SearchCollector.accepts(TargetType.IP)
