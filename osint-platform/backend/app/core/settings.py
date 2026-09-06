"""Application settings, loaded from the environment.

Every external credential and tunable lives here. Nothing is hard-coded, and
nothing in this module is ever logged (see :mod:`app.core.logging`).
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Values come from environment variables (or a local ``.env``). See
    ``.env.example`` in the repository root for documentation of every key.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- general
    app_name: str = "OSINT Investigation Platform"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    api_prefix: str = "/api/v1"
    #: Ceiling on an inbound request body. Investigation payloads are small.
    max_request_bytes: int = 1_000_000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --------------------------------------------------------------- storage
    database_url: str = "postgresql+psycopg://osint:osint@localhost:5432/osint"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    cache_enabled: bool = True

    # ---------------------------------------------------------------- celery
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False

    # ------------------------------------------------------------ networking
    http_timeout_seconds: float = 15.0
    http_max_redirects: int = 5
    http_max_response_bytes: int = 5 * 1024 * 1024
    http_user_agent: str = (
        "osint-platform/0.1 (+https://github.com/osint-platform; lawful OSINT research)"
    )
    http_max_concurrency: int = 10
    http_retry_attempts: int = 3
    http_retry_base_delay: float = 0.5

    #: Disables the SSRF guard's private-network protection. Only ever enable
    #: this for an authorised internal-security engagement.
    allow_private_networks: bool = False

    #: Global politeness ceiling applied on top of per-collector rate limits.
    default_rate_limit_per_second: float = 5.0

    # -------------------------------------------------------------- policies
    #: Respect robots.txt for the HTTP metadata / username collectors.
    respect_robots_txt: bool = True
    #: Maximum collectors executed concurrently for one target.
    collector_concurrency: int = 6
    #: Hard ceiling on a single collector run.
    collector_timeout_seconds: float = 60.0

    # ------------------------------------------------------------- providers
    search_provider: Literal["none", "brave", "bing", "serper"] = "none"
    brave_api_key: SecretStr | None = None
    bing_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    hibp_api_key: SecretStr | None = None

    crtsh_base_url: str = "https://crt.sh"
    rdap_bootstrap_url: str = "https://rdap.org"
    wayback_base_url: str = "https://web.archive.org"
    github_api_url: str = "https://api.github.com"

    # --------------------------------------------------------------- privacy
    #: Redact values classified SENSITIVE/RESTRICTED before persistence.
    privacy_redaction_enabled: bool = True
    #: Store raw collector payloads (hashed + on disk) for evidence integrity.
    evidence_store_raw: bool = True
    evidence_dir: str = "./data/evidence"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "brave_api_key",
        "bing_api_key",
        "serper_api_key",
        "github_token",
        "hibp_api_key",
        mode="before",
    )
    @classmethod
    def _blank_credential_is_absent(cls, value: object) -> object:
        """Treat a blank credential as an absent one.

        ``docker-compose.yml`` passes optional keys through as
        ``GITHUB_TOKEN: ${GITHUB_TOKEN:-}``, so an unset key arrives as an empty
        string rather than not arriving at all. Without this, the field parses
        to ``SecretStr('')``, every ``is None`` guard downstream reads it as
        *configured*, and the collector builds a credential-less header —
        ``Authorization: Bearer `` — which httpx rejects outright as an illegal
        header value. Normalising here fixes the whole class of bug at once
        instead of guarding at each use site.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests that patch the environment)."""
    get_settings.cache_clear()
