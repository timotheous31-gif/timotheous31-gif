"""Email collector: conservative by construction."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.email import EmailCollector, _gravatar_hash
from app.core.errors import CollectorError
from app.models.enums import Classification, FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return EmailCollector()


async def test_domain_finding_is_always_produced(collector, collector_ctx):
    result = await collector.collect(normalize_target("user@example.com"), collector_ctx)
    finding = result.findings[0]
    assert finding.kind is FindingKind.EMAIL_DOMAIN
    assert finding.data["domain"] == "example.com"
    assert finding.classification is Classification.PERSONAL


async def test_gravatar_url_is_a_hash_not_the_address(collector, collector_ctx):
    result = await collector.collect(normalize_target("user@example.com"), collector_ctx)
    data = result.findings[0].data
    assert data["gravatar_hash"] == _gravatar_hash("user@example.com")
    assert "user@example.com" not in data["gravatar_url"]
    assert data["gravatar_url"].startswith("https://www.gravatar.com/avatar/")


async def test_local_part_is_reduced_to_a_length(collector, collector_ctx):
    result = await collector.collect(normalize_target("alice.smith@example.com"), collector_ctx)
    data = result.findings[0].data
    assert data["local_part_length"] == len("alice.smith")
    assert "local_part" not in data


async def test_breach_check_is_skipped_without_a_key(collector, collector_ctx):
    result = await collector.collect(normalize_target("user@example.com"), collector_ctx)
    assert len(result.findings) == 1
    assert any("HIBP_API_KEY is not configured" in note for note in result.notes)


@respx.mock
async def test_breach_summary_reports_names_and_dates_only(
    collector_ctx, mock_http, monkeypatch, fixture
):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("HIBP_API_KEY", "test-key")
    reset_settings_cache()
    try:
        respx.get("https://haveibeenpwned.com/api/v3/breachedaccount/user@example.com").mock(
            return_value=httpx.Response(200, json=fixture("hibp_breaches.json"))
        )

        collector = EmailCollector()
        collector_ctx.settings = collector.settings
        result = await collector.collect(normalize_target("user@example.com"), collector_ctx)

        exposure = next(f for f in result.findings if f.kind is FindingKind.EXPOSURE_SUMMARY)
        assert exposure.data["breach_count"] == 2
        assert {b["name"] for b in exposure.data["breaches"]} == {"ExampleForum", "ExampleShop"}
        assert "Passwords" in exposure.data["exposed_data_categories"]
        assert exposure.classification is Classification.SENSITIVE
        # Categories are named; no credential material is present.
        serialized = str(exposure.data).lower()
        assert "hash" not in serialized
        assert "plaintext" not in serialized
        assert exposure.observed_at.year == 2019
    finally:
        reset_settings_cache()


@respx.mock
async def test_clean_address_produces_no_exposure_finding(collector_ctx, mock_http, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("HIBP_API_KEY", "test-key")
    reset_settings_cache()
    try:
        respx.get("https://haveibeenpwned.com/api/v3/breachedaccount/clean@example.com").mock(
            return_value=httpx.Response(404)
        )
        collector = EmailCollector()
        collector_ctx.settings = collector.settings
        result = await collector.collect(normalize_target("clean@example.com"), collector_ctx)
        assert all(f.kind is not FindingKind.EXPOSURE_SUMMARY for f in result.findings)
    finally:
        reset_settings_cache()


@respx.mock
async def test_bad_api_key_raises(collector_ctx, mock_http, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("HIBP_API_KEY", "wrong")
    reset_settings_cache()
    try:
        respx.get("https://haveibeenpwned.com/api/v3/breachedaccount/user@example.com").mock(
            return_value=httpx.Response(401)
        )
        collector = EmailCollector()
        collector_ctx.settings = collector.settings
        with pytest.raises(CollectorError, match="401"):
            await collector.collect(normalize_target("user@example.com"), collector_ctx)
    finally:
        reset_settings_cache()


def test_collector_declines_non_email_targets():
    from app.models.enums import TargetType

    assert EmailCollector.supported_targets == [TargetType.EMAIL]


def test_no_smtp_or_recovery_probing_is_implemented():
    """Guard against anyone adding mailbox verification or reset probing.

    Inspects the module's AST rather than its text, so the documentation that
    *names* these prohibitions does not trip the check.
    """
    import ast
    import inspect

    from app.collectors import email as module

    tree = ast.parse(inspect.getsource(module))

    imported: set[str] = set()
    endpoints: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "://" in node.value:
            endpoints.add(node.value.lower())

    assert not imported & {"smtplib", "poplib", "imaplib", "socket"}
    for endpoint in endpoints:
        for forbidden in ("reset", "recover", "forgot", "signin", "login", "auth"):
            assert forbidden not in endpoint, endpoint
