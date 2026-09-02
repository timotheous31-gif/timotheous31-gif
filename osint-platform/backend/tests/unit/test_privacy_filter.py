"""Privacy classification and redaction."""

from __future__ import annotations

import pytest

from app.models.enums import Classification
from app.privacy.classifier import at_least, classify_value, exceeds, max_classification
from app.privacy.filter import REDACTED_VALUE, SUPPRESSED_VALUE, PrivacyFilter


@pytest.fixture
def privacy():
    return PrivacyFilter()


@pytest.mark.parametrize(
    ("key", "value", "level"),
    [
        ("hostname", "example.com", Classification.PUBLIC),
        ("registrar", "Example Registrar LLC", Classification.PUBLIC),
        ("record_type", "A", Classification.PUBLIC),
        ("bio", "Maintains example projects", Classification.PERSONAL),
        ("full_name", "Example User", Classification.PERSONAL),
        ("email", "user@example.com", Classification.PERSONAL),
        ("location", "Documentation Land", Classification.PERSONAL),
        ("home_address", "1 Example Street", Classification.SENSITIVE),
        ("latitude", "51.5074", Classification.SENSITIVE),
        ("date_of_birth", "1990-01-01", Classification.SENSITIVE),
        ("phone", "+1-555-0100", Classification.SENSITIVE),
        ("breach_count", "2", Classification.SENSITIVE),
        ("password", "anything", Classification.RESTRICTED),
        ("api_key", "anything", Classification.RESTRICTED),
        ("private_key", "anything", Classification.RESTRICTED),
        ("ssn", "123-45-6789", Classification.RESTRICTED),
        ("iban", "GB29NWBK60161331926819", Classification.RESTRICTED),
    ],
)
def test_classification_levels(key, value, level):
    assert classify_value(key, value).level is level


def test_ordering_helpers():
    assert max_classification(Classification.PUBLIC, Classification.SENSITIVE) is (
        Classification.SENSITIVE
    )
    assert at_least(Classification.RESTRICTED, Classification.SENSITIVE)
    assert not at_least(Classification.PUBLIC, Classification.PERSONAL)
    assert exceeds(Classification.SENSITIVE, Classification.PERSONAL)
    assert not exceeds(Classification.PERSONAL, Classification.PERSONAL)
    assert max_classification() is Classification.PUBLIC


def test_credentials_are_replaced_not_stored(privacy):
    outcome = privacy.filter_finding({"api_key": "abcd1234abcd1234abcd", "title": "Example"})
    assert outcome.data["api_key"] == REDACTED_VALUE
    assert outcome.data["title"] == "Example"
    assert outcome.classification is Classification.RESTRICTED
    assert outcome.redacted is True
    assert "api_key" in outcome.redacted_fields


def test_sensitive_personal_data_is_suppressed(privacy):
    outcome = privacy.filter_finding({"home_address": "1 Example Street", "city": "Springfield"})
    assert outcome.data["home_address"] == SUPPRESSED_VALUE
    assert outcome.data["city"] == "Springfield"
    assert outcome.classification is Classification.SENSITIVE


def test_credential_shaped_value_is_caught_in_a_harmless_field(privacy):
    outcome = privacy.filter_finding({"description": "deploy with AKIA1234567890ABCDEF"})
    assert "AKIA1234567890ABCDEF" not in outcome.data["description"]
    assert outcome.classification is Classification.RESTRICTED
    assert outcome.redacted is True


def test_nested_structures_are_filtered(privacy):
    outcome = privacy.filter_finding(
        {"outer": {"inner": {"password": "x", "safe": "y"}}, "list": [{"token": "abc"}]}
    )
    assert outcome.data["outer"]["inner"]["password"] == REDACTED_VALUE
    assert outcome.data["outer"]["inner"]["safe"] == "y"
    assert outcome.data["list"][0]["token"] == REDACTED_VALUE
    assert "outer.inner.password" in outcome.redacted_fields


def test_declared_classification_is_only_raised(privacy):
    outcome = privacy.filter_finding({"title": "Example"}, declared=Classification.PERSONAL)
    assert outcome.classification is Classification.PERSONAL

    raised = privacy.filter_finding({"password": "x"}, declared=Classification.PUBLIC)
    assert raised.classification is Classification.RESTRICTED


def test_public_payload_is_untouched(privacy):
    data = {"hostname": "example.com", "records": ["93.184.215.14"], "ttl": 300}
    outcome = privacy.filter_finding(data)
    assert outcome.data == data
    assert outcome.classification is Classification.PUBLIC
    assert outcome.redacted is False


def test_provenance_urls_survive_filtering(privacy):
    outcome = privacy.filter_finding(
        {"source_url": "https://example.com/a", "profile_url": "https://github.com/x"}
    )
    assert outcome.data["source_url"] == "https://example.com/a"
    assert outcome.data["profile_url"] == "https://github.com/x"


def test_reasons_explain_each_removal(privacy):
    outcome = privacy.filter_finding({"password": "x", "latitude": "51.5"})
    joined = " ".join(outcome.reasons).lower()
    assert "credential" in joined
    assert "coordinates" in joined


def test_over_long_strings_are_truncated(privacy):
    outcome = privacy.filter_finding({"summary": "x" * 10_000})
    assert len(outcome.data["summary"]) < 10_000
    assert outcome.data["summary"].endswith("[truncated]")


def test_deeply_nested_structures_are_bounded(privacy):
    payload: dict = {"a": {}}
    node = payload["a"]
    for _ in range(30):
        node["a"] = {}
        node = node["a"]
    outcome = privacy.filter_finding(payload)
    assert "TRUNCATED" in str(outcome.data)


def test_disabling_redaction_keeps_values_but_says_so(monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("PRIVACY_REDACTION_ENABLED", "false")
    reset_settings_cache()
    try:
        outcome = PrivacyFilter().filter_finding({"password": "hunter2"})
        assert outcome.data["password"] == "hunter2"
        assert outcome.classification is Classification.RESTRICTED
        assert any("not removed" in reason for reason in outcome.reasons)
    finally:
        reset_settings_cache()


def test_export_policy_withholds_stricter_content(privacy):
    data = {"breach_count": 2}
    withheld, was_withheld = privacy.filter_for_export(
        data, classification=Classification.SENSITIVE, max_level=Classification.PERSONAL
    )
    assert was_withheld is True
    assert withheld["withheld"] is True
    assert "SENSITIVE" in withheld["classification"]

    kept, untouched = privacy.filter_for_export(
        data, classification=Classification.PUBLIC, max_level=Classification.PERSONAL
    )
    assert untouched is False
    assert kept == data


def test_excerpt_is_credential_free(privacy):
    excerpt = privacy.excerpt("token ghp_" + "b" * 36 + " tail")
    assert "ghp_" not in excerpt
    assert excerpt.startswith("token")


def test_excerpt_respects_the_limit(privacy):
    assert len(privacy.excerpt("y" * 900, limit=100)) <= 101
