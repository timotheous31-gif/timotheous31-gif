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

    # --- search-result enrichment -------------------------------------------
    #: Some providers return a URL and a title and no description at all. Rather
    #: than invent a description, the platform may fetch a *small* number of
    #: high-value result pages through the same SSRF-guarded client every
    #: collector uses, and extract a short public text excerpt. Strictly bounded:
    #: an uncapped fetch of every search result is a crawler, and this is not
    #: one.
    result_enrichment_enabled: bool = True
    max_result_enrichments_per_investigation: int = 3
    #: Byte ceiling on an enrichment fetch, and the length of the excerpt kept.
    result_enrichment_max_bytes: int = 750_000
    result_enrichment_excerpt_chars: int = 600

    # ------------------------------------------------------------- providers
    search_provider: Literal[
        "none", "anthropic_web_search", "google_wss", "brave", "bing", "serper"
    ] = "none"
    brave_api_key: SecretStr | None = None
    bing_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    hibp_api_key: SecretStr | None = None

    # --- Anthropic web search ------------------------------------------------
    # A *secondary* discovery channel. Anthropic's server-side web search is not
    # a deterministic query API: the model decides which searches to run, there
    # is no parameter that submits an exact query, and results carry no
    # description and no documented ranking. Everything below exists to keep that
    # channel bounded and honestly accounted for. Required only when
    # SEARCH_PROVIDER=anthropic_web_search; every other mode, including the
    # zero-cost default, never reads these.
    anthropic_api_key: SecretStr | None = None
    anthropic_api_url: str = "https://api.anthropic.com"
    #: Sent as ``anthropic-version``. The documented stable value.
    anthropic_api_version: str = "2023-06-01"
    #: The model that drives the search tool. Anthropic's web-search
    #: documentation uses this model in every example on the page, and it is a
    #: current Active model in the published catalogue. Configurable because the
    #: documentation does not enumerate per-model web-search support, so an
    #: operator who wants a cheaper model verifies it for their own account.
    anthropic_web_search_model: str = "claude-opus-5"
    #: Hard ceiling on searches per request, enforced by Anthropic itself. This
    #: is the spend cap: an exceeded ceiling returns a tool-result error and
    #: Anthropic does not bill a search that errored.
    anthropic_web_search_max_uses: int = 4
    #: Output-token ceiling for the driving request. The model's prose is
    #: discarded — only the search-result blocks are ingested — so this needs to
    #: be just large enough for the turn to complete its searches.
    anthropic_web_search_max_tokens: int = 4096
    #: Published price per search operation, used for the cost *estimate*. Never
    #: presented as a billed total. $10 per 1,000 searches at time of writing.
    anthropic_web_search_unit_cost_usd: float = 0.01
    #: Domain control. Anthropic accepts one or the other and rejects a request
    #: carrying both, so supplying both here is a configuration error.
    anthropic_web_search_allowed_domains: list[str] = Field(default_factory=list)
    anthropic_web_search_blocked_domains: list[str] = Field(default_factory=list)
    #: Coarse localisation, passed through as the documented ``user_location``.
    #: City / region / ISO 3166-1 alpha-2 country / IANA timezone only. Nothing
    #: here is derived from the investigator's address or IP, and nothing
    #: narrower than a city is accepted.
    anthropic_web_search_city: str | None = None
    anthropic_web_search_region: str | None = None
    anthropic_web_search_country: str | None = None
    anthropic_web_search_timezone: str | None = None

    # --- Google Web Search Service ------------------------------------------
    # Configuration and adapter only. The provider is PENDING_PARTNER_ACCESS: it
    # can be configured and inspected but refuses to issue a request, because
    # partner credentials do not exist yet and faking access would be worse than
    # not having it.
    google_wss_api_key: SecretStr | None = None
    google_wss_client_id: str | None = None
    google_wss_endpoint: str = ""

    # --- free person sources -------------------------------------------------
    # Every endpoint below is public and needs no key or account. They are
    # settings only so a deployment can point at a mirror or a test double.
    orcid_api_url: str = "https://pub.orcid.org"
    openalex_api_url: str = "https://api.openalex.org"
    crossref_api_url: str = "https://api.crossref.org"
    wikidata_api_url: str = "https://www.wikidata.org/w/api.php"
    reddit_base_url: str = "https://www.reddit.com"
    #: Contact addresses for the OpenAlex and Crossref "polite pools". These are
    #: courtesy identifiers, not credentials: they buy faster service, and both
    #: APIs work without them.
    openalex_mailto: str | None = None
    crossref_mailto: str | None = None

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

    @field_validator(
        "cors_origins",
        "anthropic_web_search_allowed_domains",
        "anthropic_web_search_blocked_domains",
        mode="before",
    )
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept a comma-separated string for any list-valued setting.

        Environment variables are strings, and ``docker-compose.yml`` passes
        these through as one. A blank value is an empty list, not a list
        containing an empty entry.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "brave_api_key",
        "bing_api_key",
        "serper_api_key",
        "github_token",
        "hibp_api_key",
        "anthropic_api_key",
        "google_wss_api_key",
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
