"""Application settings, loaded from the environment.

Every external credential and tunable lives here. Nothing is hard-coded, and
nothing in this module is ever logged (see :mod:`app.core.logging`).
"""

from __future__ import annotations

import functools
import secrets
from typing import Annotated, ClassVar, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class UnsafeProductionConfig(RuntimeError):
    """A production deployment whose configuration would be unsafe to serve.

    Its own type, not ``ValueError``, so it cannot be caught by a
    ``pydantic.ValidationError`` handler and quietly turned into a 422 — and so
    it is raised outside model validation, where pydantic would otherwise attach
    the whole settings dict (credentials included) to the message.
    """


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
    #: Serve the interactive API documentation. On by default because it is how a
    #: developer learns the API, and **refused outright in production** (see
    #: :meth:`production_problems`): it is the only page on this origin that
    #: executes a script and the only reason the CSP has an exception at all.
    #: Turning it off there is not a recommendation, it is a start-up condition.
    docs_enabled: bool = True
    #: Ceiling on an inbound request body. Investigation payloads are small.
    max_request_bytes: int = 1_000_000
    #: ``NoDecode`` because pydantic-settings otherwise JSON-decodes a list-typed
    #: environment variable *before* any validator runs — so
    #: ``CORS_ORIGINS=https://app.example.com`` died at startup with a JSON parse
    #: error naming neither the variable nor the expected format. That is the
    #: first thing an operator sets on a real deployment, so it is the first thing
    #: that used to break. With NoDecode the raw string reaches
    #: :meth:`_split_origins`, which splits on commas.
    cors_origins: Annotated[list[str], NoDecode] = Field(
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

    # --------------------------------------------------------- authentication
    #: Keys the CSRF token derivation and any other keyed construction. There is
    #: deliberately **no usable default**: production refuses to start without
    #: one (see :meth:`_reject_unsafe_production`), and development generates an
    #: ephemeral one per process so a developer is never tempted to ship the
    #: placeholder they found in a config file.
    session_secret: SecretStr | None = None
    #: How long a session stays valid after sign-in.
    session_lifetime_hours: int = 12
    #: Idle timeout. A session unused for this long is refused even if it has not
    #: reached its absolute lifetime.
    session_idle_timeout_minutes: int = 120
    #: Cookie names. Only the session cookie is HttpOnly; the CSRF cookie must be
    #: readable by the page so it can be echoed in a header.
    session_cookie_name: str = "osint_session"
    csrf_cookie_name: str = "osint_csrf"
    #: ``Secure`` on the session cookie. Forced on in production; off by default
    #: in development so http://localhost works without a certificate.
    session_cookie_secure: bool | None = None
    #: ``SameSite``. ``lax`` lets the dev frontend on :3000 reach the API on
    #: :8000 (same site, different port) while still refusing cross-site POSTs.
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    #: Set when the API and the frontend are served from different hosts under one
    #: registrable domain. Left unset the cookie is host-only, which is stricter.
    session_cookie_domain: str | None = None

    # ---------------------------------------------------------- rate limiting
    #: Inbound request throttling. Distinct from the *outbound* collector limits
    #: in :mod:`app.core.ratelimit`, which exist to be polite to third parties.
    rate_limit_enabled: bool = True
    #: Failed sign-ins per account and per client address before a cooldown.
    login_max_attempts: int = 8
    login_attempt_window_seconds: int = 900
    #: Bounded, so a wrong password cannot be used to lock somebody out for good.
    login_lockout_seconds: int = 900
    #: Per-user ceilings on the expensive operations. Generous enough that normal
    #: analyst work never notices them.
    rate_limit_case_create_per_hour: int = 60
    rate_limit_investigation_per_hour: int = 60
    rate_limit_import_per_hour: int = 300
    rate_limit_report_per_hour: int = 120
    rate_limit_recon_per_hour: int = 120

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
    #: ``NoDecode`` for the same reason ``cors_origins`` carries it: without it
    #: pydantic-settings JSON-decodes a list-typed environment variable *before*
    #: any validator runs, so ``…ALLOWED_DOMAINS=a.com,b.com`` died at startup
    #: with a parse error naming neither the variable nor the expected format —
    #: and :meth:`_split_list` never got to see it.
    anthropic_web_search_allowed_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    anthropic_web_search_blocked_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
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
        """Accept ``a,b`` or ``["a","b"]`` for any list-valued setting.

        The comma form is what ``.env.example`` and ``docker-compose.yml`` use and
        what an operator will type. The JSON form is accepted too, because it is
        what pydantic-settings used to require and a deployment may already have
        one written down. A blank value is an empty list, not a list containing
        an empty entry.

        One implementation for every list setting: the origin list this began as,
        and the Anthropic domain filters. They arrive the same way — an
        environment variable, typed by hand — so they should not have two
        different ideas of what an operator is allowed to write.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            import json

            try:
                decoded = json.loads(text)
            except ValueError:
                pass
            else:
                if isinstance(decoded, list):
                    return [str(item).strip() for item in decoded if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator(
        "brave_api_key",
        "bing_api_key",
        "serper_api_key",
        "github_token",
        "hibp_api_key",
        "session_secret",
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

    # ------------------------------------------------- production safety gate
    #: Session secrets that are obviously placeholders. Rejected in production by
    #: value, so copying an example file cannot produce a deployment whose CSRF
    #: tokens every reader of this repository can forge.
    UNSAFE_SECRETS: ClassVar[frozenset[str]] = frozenset(
        {
            "change-me",
            "changeme",
            "secret",
            "session-secret",
            "dev",
            "development",
            "test",
            "insecure",
            "please-change-me",
            "replace-me",
            "your-secret-here",
        }
    )
    #: Minimum length for a production session secret. 32 characters of output
    #: from ``python -c "import secrets; print(secrets.token_urlsafe(48))"``.
    MIN_SECRET_LENGTH: ClassVar[int] = 32

    #: An operator's explicit acknowledgement that private-network fetching is
    #: authorised for this engagement. Required in production alongside
    #: ``ALLOW_PRIVATE_NETWORKS=true``; without it the two together are a
    #: configuration error rather than a silently-honoured request.
    private_network_authorization: str | None = None

    def production_problems(self) -> list[str]:
        """Every reason this configuration is unsafe to run as production.

        Returns a list rather than raising, for two reasons: a test can assert on
        the individual findings, and the caller can present all of them at once
        instead of making an operator fix them one restart at a time.

        Nothing here is repaired automatically. A deployment that quietly fixes
        its own dangerous settings teaches its operator that the settings do not
        matter; one that refuses to start teaches them exactly which line to fix.
        Each message names the variable and what to set it to.
        """
        problems: list[str] = []
        if self.environment != "production":
            return problems

        secret = self.session_secret.get_secret_value() if self.session_secret else ""
        if not secret:
            problems.append(
                "SESSION_SECRET is not set. Generate one with:\n"
                '    python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        elif secret.strip().lower() in self.UNSAFE_SECRETS:
            problems.append(
                "SESSION_SECRET is a well-known placeholder. Anyone who has read "
                "this repository could forge a CSRF token. Generate a real one."
            )
        elif len(secret) < self.MIN_SECRET_LENGTH:
            problems.append(f"SESSION_SECRET is shorter than {self.MIN_SECRET_LENGTH} characters.")

        if self.debug:
            problems.append("DEBUG must be false in production.")

        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS contains '*'. A credentialed API cannot use a wildcard "
                "origin; list the frontend origins explicitly."
            )
        local = [
            origin for origin in self.cors_origins if "localhost" in origin or "127.0.0.1" in origin
        ]
        if local:
            problems.append(
                f"CORS_ORIGINS still contains development origins ({', '.join(local)}). "
                f"List only the deployed frontend origins."
            )
        insecure = [origin for origin in self.cors_origins if origin.startswith("http://")]
        if insecure:
            problems.append(
                f"CORS_ORIGINS contains plaintext origins ({', '.join(insecure)}). "
                f"A session cookie marked Secure will not be sent to them."
            )

        if self.allow_private_networks and not (self.private_network_authorization or "").strip():
            problems.append(
                "ALLOW_PRIVATE_NETWORKS=true disables the SSRF guard's private-network "
                "protection. In production it additionally requires "
                "PRIVATE_NETWORK_AUTHORIZATION to be set to a reference for the "
                "engagement that authorised it (a ticket, a contract, a name)."
            )

        if self.session_cookie_secure is False:
            problems.append(
                "SESSION_COOKIE_SECURE=false would send the session cookie over "
                "plaintext HTTP. Leave it unset in production; it defaults to true."
            )
        if self.session_cookie_samesite == "none" and not self.cookies_secure:
            problems.append("SESSION_COOKIE_SAMESITE=none requires a Secure cookie.")

        if not self.rate_limit_enabled:
            problems.append(
                "RATE_LIMIT_ENABLED=false leaves sign-in unthrottled. Keep it on in " "production."
            )

        if self.docs_enabled:
            problems.append(
                "DOCS_ENABLED=true serves Swagger UI at /docs. It is the only page on "
                "this origin that executes a script, and the only reason the "
                "Content-Security-Policy has a CDN exception at all. Set "
                "DOCS_ENABLED=false in production."
            )

        problems.extend(self._search_provider_problems())

        return problems

    def _search_provider_problems(self) -> list[str]:
        """Whether the selected search provider could actually run.

        ``SEARCH_PROVIDER=none`` is the supported default and never a problem:
        every free structured source and the manual workflow work without one.
        But a deployment that *names* a provider has said it wants the public web
        searched, and in production that must be true on the day it starts rather
        than discovered later from a report that says the web was never searched.

        The question is put to the provider rather than answered here. Each one
        already declares what it needs through ``is_available()`` — a missing
        credential, an activation state that is not ACTIVE, Anthropic's
        mutually-exclusive domain lists, a search budget below one — and
        duplicating any of that in this module would be a second, staler copy of
        the same rules.
        """
        if self.search_provider == "none":
            return []
        try:
            from app.services.providers.search import get_search_provider

            available, reason = get_search_provider(self).is_available()
        except Exception as exc:  # pragma: no cover - provider import or construction error
            return [
                f"SEARCH_PROVIDER={self.search_provider} could not be constructed "
                f"({type(exc).__name__}). Set SEARCH_PROVIDER=none or fix the "
                f"provider configuration."
            ]
        if available:
            return []
        return [
            f"SEARCH_PROVIDER={self.search_provider} is selected but not usable: "
            f"{reason} Set SEARCH_PROVIDER=none to run without a provider, or "
            f"supply what it needs."
        ]

    def require_safe_production(self) -> None:
        """Raise :class:`UnsafeProductionConfig` if this deployment must not run."""
        problems = self.production_problems()
        if not problems:
            return
        listed = "\n\n".join(f"  {index}. {item}" for index, item in enumerate(problems, 1))
        raise UnsafeProductionConfig(
            "Refusing to start: ENVIRONMENT=production with unsafe configuration.\n\n"
            f"{listed}\n\n"
            "Nothing here has been adjusted for you — a production deployment that "
            "silently repairs its own security settings is worse than one that will "
            "not start. See docs/pilot-deployment.md."
        )

    @property
    def cookies_secure(self) -> bool:
        """Whether to mark cookies ``Secure``.

        Production is always secure. Development follows the explicit setting and
        otherwise stays off, so ``http://localhost`` works without a certificate —
        which is the whole reason this is not simply hard-coded to true.
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.environment == "production"

    @property
    def hsts_enabled(self) -> bool:
        """Send HSTS only where HTTPS is actually terminated.

        Sending it in development would teach a developer's browser to refuse
        ``http://localhost`` for months, which is a self-inflicted outage rather
        than a security control.
        """
        return self.environment == "production" and self.cookies_secure

    def session_secret_value(self) -> str:
        """The signing secret, or a per-process ephemeral one in development.

        An ephemeral secret means every restart invalidates outstanding CSRF
        tokens, which is a small annoyance locally and strictly better than a
        checked-in default that reaches production.
        """
        if self.session_secret is not None:
            value = self.session_secret.get_secret_value()
            if value:
                return value
        return _ephemeral_secret()

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
def _ephemeral_secret() -> str:
    """A random secret for this process only. Never used in production."""
    return secrets.token_urlsafe(48)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    The production gate runs here rather than in the API's startup hook, so the
    worker and the CLI are held to the same standard as the web process. A
    deployment cannot get a dangerous configuration past this by entering through
    a different door.
    """
    settings = Settings()
    settings.require_safe_production()
    return settings


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests that patch the environment)."""
    get_settings.cache_clear()
