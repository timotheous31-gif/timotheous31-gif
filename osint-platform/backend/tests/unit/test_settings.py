"""Settings loading and validation."""

from __future__ import annotations

import pytest

from app.core.settings import Settings


def test_defaults_are_safe():
    settings = Settings(_env_file=None)
    assert settings.allow_private_networks is False
    assert settings.privacy_redaction_enabled is True
    assert settings.search_provider == "none"
    assert settings.http_max_response_bytes <= 10 * 1024 * 1024


def test_cors_origins_accepts_comma_separated_string():
    settings = Settings(_env_file=None, cors_origins="http://a.test,http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_broker_falls_back_to_redis_url():
    settings = Settings(_env_file=None, redis_url="redis://cache:6379/2")
    assert settings.broker_url == "redis://cache:6379/2"
    assert settings.result_backend == "redis://cache:6379/2"


def test_secrets_are_not_exposed_by_repr():
    settings = Settings(_env_file=None, github_token="ghp_supersecretvalue000000")
    assert "ghp_supersecretvalue" not in repr(settings)
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_supersecretvalue000000"


@pytest.mark.parametrize("value", ["development", "test", "production"])
def test_environment_choices(value):
    assert Settings(_env_file=None, environment=value).environment == value
