"""The controls that are not authorization, asserted against the running app.

Workspace containment and role enforcement have their own files
(:mod:`tests.integration.test_workspace_isolation`,
:mod:`tests.integration.test_authentication`). What is left is everything a
pilot customer's reviewer would ask about *besides* who may read what:

* throttling, and — the part that matters for an investigation product — that a
  refusal reads as a refusal rather than as an empty result;
* the browser-facing headers: CSP, HSTS, framing, and a credentialed CORS policy
  that never answers with a wildcard;
* the audit ledger, including the filter that keeps credentials out of it;
* report and evidence handling, which is where investigator-supplied text meets
  a renderer;
* the production configuration gate, which must refuse rather than repair.

These are written as behaviour against the ASGI app wherever that is possible,
because a constant asserted against itself proves nothing about what is served.
"""

from __future__ import annotations

import pytest

from app.core import throttle
from app.core.settings import Settings, UnsafeProductionConfig
from app.models.enums import AuditEvent
from tests.conftest import TEST_PASSWORD, _bootstrap_account


@pytest.fixture(autouse=True)
def _fresh_throttle():
    """Give every test its own counters.

    The backend is process-global and the suite shares a process, so without this
    a test that exhausts a limit leaves the next one pre-throttled — which shows
    up as an unrelated 429 in a file that never mentions rate limiting.
    """
    backend = throttle.MemoryThrottle()
    throttle.set_throttle(backend)
    yield backend
    throttle.set_throttle(None)


def _limit(name: str, value: int) -> None:
    """Lower one rate limit on the live settings singleton."""
    from app.core.settings import get_settings

    object.__setattr__(get_settings(), name, value)


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------


class TestSignInThrottling:
    async def test_repeated_wrong_passwords_are_eventually_refused(self, anonymous_client):
        _bootstrap_account()
        _limit("login_max_attempts", 3)

        codes = []
        for _ in range(6):
            response = await anonymous_client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@example.com", "password": "not the password"},
            )
            codes.append(response.status_code)

        assert 429 in codes, codes
        # The first few are ordinary credential failures, not throttles: a limit
        # that engaged on attempt one would lock out anyone with a typo.
        assert codes[0] == 401

    async def test_a_throttled_sign_in_says_when_to_come_back(self, anonymous_client):
        _bootstrap_account()
        _limit("login_max_attempts", 1)

        for _ in range(4):
            response = await anonymous_client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@example.com", "password": "wrong"},
            )
            if response.status_code == 429:
                break

        assert response.status_code == 429
        assert response.json()["code"] == "rate_limited"
        # Without Retry-After a client can only guess, and guessing means
        # hammering the endpoint that just asked it to stop.
        assert int(response.headers["Retry-After"]) > 0

    async def test_a_lockout_is_not_permanent(self, anonymous_client, _fresh_throttle):
        """A wrong password must not be a denial-of-service against its owner."""
        _bootstrap_account()
        _limit("login_max_attempts", 1)

        for _ in range(3):
            await anonymous_client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@example.com", "password": "wrong"},
            )
        blocked = await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@example.com", "password": TEST_PASSWORD},
        )
        assert blocked.status_code == 429

        # The cooldown expires on its own. Simulated by expiring the window
        # rather than by sleeping for it.
        _fresh_throttle.clear()

        recovered = await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@example.com", "password": TEST_PASSWORD},
        )
        assert recovered.status_code == 200

    async def test_a_correct_password_clears_the_counter(self, anonymous_client, _fresh_throttle):
        """One forgotten password must not ration the rest of the day."""
        _bootstrap_account()
        _limit("login_max_attempts", 4)

        for _ in range(2):
            await anonymous_client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@example.com", "password": "wrong"},
            )
        good = await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@example.com", "password": TEST_PASSWORD},
        )
        assert good.status_code == 200

        key = throttle.principal_key("login:account", "analyst@example.com")
        assert _fresh_throttle.get(key) == (0, 0)

    async def test_the_throttle_does_not_store_who_it_is_throttling(self, _fresh_throttle):
        """Keys are digests. A readable Redis must not become a list of users."""
        key = throttle.principal_key("login:account", "analyst@example.com")
        assert "analyst@example.com" not in key
        assert "analyst" not in key
        assert key.startswith(throttle.KEY_PREFIX)
        # Same input, same bucket — otherwise the limit would never engage.
        assert key == throttle.principal_key("login:account", "Analyst@Example.com ")

    async def test_a_throttled_sign_in_is_recorded(self, anonymous_client):
        _bootstrap_account()
        _limit("login_max_attempts", 1)
        for _ in range(4):
            await anonymous_client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@example.com", "password": "wrong"},
            )

        events = _audit_events()
        assert AuditEvent.RATE_LIMIT_TRIGGERED in events
        assert AuditEvent.USER_LOGIN_FAILURE in events

    async def test_a_failed_sign_in_does_not_record_the_address_tried(self, anonymous_client):
        """Otherwise the failure log becomes a list of who might have an account."""
        _bootstrap_account()
        await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "someone-else@example.com", "password": "wrong"},
        )
        for entry in _audit_entries():
            assert "someone-else@example.com" not in str(entry.metadata_)


class TestExpensiveOperationThrottling:
    async def test_case_creation_is_bounded(self, api_client):
        _limit("rate_limit_case_create_per_hour", 2)

        codes = [
            (await api_client.post("/api/v1/cases", json={"name": f"Case {index}"})).status_code
            for index in range(4)
        ]
        assert codes[:2] == [201, 201]
        assert 429 in codes[2:]

    async def test_report_rendering_is_bounded(self, api_client, case_id):
        _limit("rate_limit_report_per_hour", 1)

        first = await api_client.get(f"/api/v1/cases/{case_id}/report")
        assert first.status_code == 200
        second = await api_client.get(f"/api/v1/cases/{case_id}/report")
        assert second.status_code == 429
        assert second.json()["code"] == "rate_limited"

    async def test_a_throttled_search_is_not_an_empty_result(self, api_client, case_id):
        """The one throttling behaviour that would corrupt an investigation.

        A refused search must never reach the report as "the public web returned
        nothing about this person". It is a 429 with a body that says nothing was
        searched — never a 200 with zero results.
        """
        created = await api_client.post(
            f"/api/v1/cases/{case_id}/targets",
            json={"value": "Ada Lovelace", "type": "PERSON"},
        )
        assert created.status_code == 201, created.text
        target_id = created.json()["id"]

        _limit("rate_limit_recon_per_hour", 1)
        first = await api_client.post(f"/api/v1/cases/{case_id}/targets/{target_id}/search")
        assert first.status_code == 200
        response = await api_client.post(f"/api/v1/cases/{case_id}/targets/{target_id}/search")
        assert response.status_code == 429
        body = response.json()
        assert body["code"] == "rate_limited"
        assert "not a result" in body["message"].lower()
        # Nothing that a coverage renderer could mistake for an answer.
        assert "results_seen" not in body
        assert "no_match" not in response.text.lower()

    def test_a_limit_of_zero_means_unlimited_not_blocked(self):
        """Pinned because the reading is not obvious from the variable name.

        ``check`` treats a non-positive limit as "no limit configured", so an
        operator who sets ``RATE_LIMIT_RECON_PER_HOUR=0`` intending to forbid
        provider searches gets the opposite. The way to forbid them is to leave
        ``SEARCH_PROVIDER=none``; the way to turn throttling off is
        ``RATE_LIMIT_ENABLED=false``. Both are documented in
        ``docs/pilot-deployment.md``.
        """
        verdict = throttle.check("osint:throttle:test:zero", limit=0, window_seconds=60)
        assert verdict.allowed is True

    async def test_limits_are_per_user_not_global(self, api_client, anonymous_client):
        """One noisy analyst must not throttle their colleague."""
        _limit("rate_limit_case_create_per_hour", 1)
        assert (await api_client.post("/api/v1/cases", json={"name": "First"})).status_code == 201
        assert (await api_client.post("/api/v1/cases", json={"name": "Second"})).status_code == 429

        import httpx

        from app.main import create_app

        _bootstrap_account(email="colleague@example.com", workspace="Other Workspace")
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
            signin = await other.post(
                "/api/v1/auth/login",
                json={"email": "colleague@example.com", "password": TEST_PASSWORD},
            )
            assert signin.status_code == 200, signin.text
            other.headers["X-CSRF-Token"] = signin.json()["csrf_token"]
            mine = await other.post("/api/v1/cases", json={"name": "Theirs"})
        assert mine.status_code == 201


# --------------------------------------------------------------------------
# Browser-facing headers
# --------------------------------------------------------------------------


class TestSecurityHeaders:
    async def test_the_api_denies_everything_by_default(self, api_client):
        response = await api_client.get("/api/v1/cases")
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "base-uri 'none'" in policy
        # The thing the specification explicitly forbade.
        assert "script-src *" not in policy
        assert "unsafe-eval" not in policy

    async def test_framing_is_refused_two_ways(self, api_client):
        response = await api_client.get("/health")
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

    async def test_the_documentation_page_gets_its_own_policy(self, api_client):
        """The one exception, and it is named rather than wildcarded."""
        response = await api_client.get("/docs")
        assert response.status_code == 200
        policy = response.headers["Content-Security-Policy"]
        assert "https://cdn.jsdelivr.net" in policy
        assert "script-src *" not in policy
        # And it does not leak back onto the API's own responses.
        api = await api_client.get("/api/v1/cases")
        assert "cdn.jsdelivr.net" not in api.headers["Content-Security-Policy"]

    async def test_documentation_can_be_turned_off_entirely(self, monkeypatch):
        import httpx

        from app.core.settings import reset_settings_cache
        from app.main import create_app

        monkeypatch.setenv("DOCS_ENABLED", "false")
        reset_settings_cache()
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert (await client.get(path)).status_code == 404, path

    async def test_hsts_is_absent_in_development(self, api_client):
        """Sending it on localhost would break http:// for months."""
        response = await api_client.get("/health")
        assert "Strict-Transport-Security" not in response.headers

    async def test_hsts_is_sent_in_production(self):
        """Asserted on the property the middleware reads, not on a constant."""
        production = Settings(
            environment="production",
            session_secret="x" * 48,
            cors_origins=["https://app.example.com"],
        )
        assert production.hsts_enabled is True
        assert production.cookies_secure is True

        development = Settings(environment="development")
        assert development.hsts_enabled is False
        assert development.cookies_secure is False

    async def test_responses_are_not_sniffable_or_referrer_leaking(self, api_client):
        response = await api_client.get("/api/v1/cases")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-site"


class TestCors:
    async def test_a_configured_origin_is_allowed_with_credentials(self, api_client):
        response = await api_client.options(
            "/api/v1/cases",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert response.headers["access-control-allow-credentials"] == "true"

    async def test_the_wildcard_is_never_the_answer(self, api_client):
        """A credentialed API answering ``*`` is the classic CORS mistake."""
        response = await api_client.options(
            "/api/v1/cases",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers["access-control-allow-origin"] != "*"

    async def test_an_unlisted_origin_is_not_echoed_back(self, api_client):
        response = await api_client.options(
            "/api/v1/cases",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "https://evil.example"

    async def test_a_cross_site_read_gets_no_permission_to_read_it(self, api_client):
        """The header the browser actually enforces on a simple GET."""
        response = await api_client.get("/api/v1/cases", headers={"Origin": "https://evil.example"})
        assert response.headers.get("access-control-allow-origin") is None


# --------------------------------------------------------------------------
# The audit ledger
# --------------------------------------------------------------------------


def _audit_entries():
    from app.core.db import get_session_factory
    from app.models.auth import AuditLogEntry

    with get_session_factory()() as session:
        return list(session.query(AuditLogEntry).all())


def _audit_events() -> set[str]:
    return {entry.event_type for entry in _audit_entries()}


class TestSecretRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "new_password",
            "api_key",
            "apiKey",
            "x-api-key",
            "X-API-KEY",
            "authorization",
            "Authorization",
            "auth_header",
            "session_token",
            "sessionToken",
            "csrf_token",
            "cookie",
            "Set-Cookie",
            "anthropic_api_key",
            "google_credential",
            "bearer_token",
            "private_key",
            "password_hash",
            "client.secret",
        ],
    )
    def test_a_credential_shaped_key_never_reaches_the_ledger(self, key):
        from app.services.audit import safe_metadata

        kept = safe_metadata({key: "s3cret-value", "case_name": "Example"})
        assert key not in kept
        assert "s3cret-value" not in str(kept)
        # The entry says it lost something rather than looking complete.
        assert kept["_dropped_fields"] == 1
        assert kept["case_name"] == "Example"

    def test_an_ordinary_field_survives(self):
        from app.services.audit import safe_metadata

        kept = safe_metadata({"format": "html", "results": 4, "ok": True})
        assert kept == {"format": "html", "results": 4, "ok": True}

    def test_a_whole_request_body_is_filtered_rather_than_trusted(self):
        """The caller does not have to remember. The filter does."""
        from app.services.audit import safe_metadata

        kept = safe_metadata(
            {
                "email": "analyst@example.com",
                "password": TEST_PASSWORD,
                "remember": True,
            }
        )
        assert TEST_PASSWORD not in str(kept)
        assert kept["email"] == "analyst@example.com"

    def test_values_are_bounded(self):
        from app.services.audit import MAX_METADATA_KEYS, MAX_VALUE_LENGTH, safe_metadata

        kept = safe_metadata({"note": "x" * 5000})
        assert len(kept["note"]) == MAX_VALUE_LENGTH

        many = safe_metadata({f"field_{index}": index for index in range(MAX_METADATA_KEYS + 10)})
        assert len(many) <= MAX_METADATA_KEYS + 1  # + the dropped-field count
        assert many["_dropped_fields"] == 10

    def test_an_object_cannot_be_smuggled_in_whole(self):
        from app.services.audit import safe_metadata

        class Carrier:
            def __init__(self) -> None:
                self.token = "should-not-be-reachable"

        kept = safe_metadata({"payload": Carrier()})
        assert isinstance(kept["payload"], str)

    async def test_a_sign_in_never_writes_the_password_anywhere(self, anonymous_client):
        _bootstrap_account()
        await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@example.com", "password": TEST_PASSWORD},
        )
        for entry in _audit_entries():
            assert TEST_PASSWORD not in str(entry.metadata_)

    async def test_a_session_token_is_stored_only_as_a_digest(self, anonymous_client):
        """A stolen database backup must not contain usable session tokens."""
        from app.core.db import get_session_factory
        from app.models.auth import UserSession

        _bootstrap_account()
        response = await anonymous_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@example.com", "password": TEST_PASSWORD},
        )
        assert response.status_code == 200
        cookie = response.cookies["osint_session"]
        assert cookie

        with get_session_factory()() as session:
            rows = session.query(UserSession).all()
        assert rows
        for row in rows:
            assert cookie not in str(row.__dict__)
            # SHA-256, hex.
            assert len(row.token_hash) == 64
            assert row.token_hash != cookie


class TestAuditEvents:
    async def test_signing_in_and_out_are_both_recorded(self, api_client):
        assert AuditEvent.USER_LOGIN_SUCCESS in _audit_events()
        assert (await api_client.post("/api/v1/auth/logout")).status_code == 204
        assert AuditEvent.USER_LOGOUT in _audit_events()

    async def test_creating_and_deleting_a_case_are_recorded(self, api_client, case_id):
        assert AuditEvent.CASE_CREATED in _audit_events()
        assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204
        assert AuditEvent.CASE_DELETED in _audit_events()

    async def test_rendering_a_report_is_recorded(self, api_client, case_id):
        assert (await api_client.get(f"/api/v1/cases/{case_id}/report")).status_code == 200
        assert AuditEvent.REPORT_GENERATED in _audit_events()

    async def test_an_entry_carries_who_what_and_which_request(self, api_client, case_id):
        entries = [e for e in _audit_entries() if e.event_type == AuditEvent.CASE_CREATED]
        assert entries
        entry = entries[-1]
        assert entry.actor_user_id is not None
        assert entry.workspace_id is not None
        assert entry.object_type == "case"
        assert entry.object_id == str(case_id)
        # Correlates the ledger with the structured application log.
        assert entry.request_id

    async def test_the_ledger_has_no_update_or_delete_route(self):
        """Append-only is a property of the API surface, not just of intent."""
        from app.main import create_app

        spec = create_app().openapi()
        for path, operations in spec["paths"].items():
            if "audit" not in path:
                continue
            assert set(operations) <= {"get"}, (path, sorted(operations))

    async def test_an_entry_survives_the_user_that_made_it(self, api_client, case_id):
        """Deleting an account must not erase what it did."""
        from app.core.db import get_session_factory
        from app.models.auth import AuditLogEntry, User

        before = len(_audit_entries())
        assert before

        with get_session_factory()() as session:
            user = session.query(User).filter(User.email == "analyst@example.com").one()
            session.delete(user)
            session.commit()

        with get_session_factory()() as session:
            remaining = session.query(AuditLogEntry).all()
            assert len(remaining) == before
            assert any(entry.actor_user_id is None for entry in remaining)


# --------------------------------------------------------------------------
# Reports, evidence and investigator-supplied text
# --------------------------------------------------------------------------


class TestReportHandling:
    async def test_a_report_is_an_attachment_under_its_own_policy(self, api_client, case_id):
        response = await api_client.get(f"/api/v1/cases/{case_id}/report?report_format=html")
        assert response.status_code == 200
        assert response.headers["Content-Disposition"].startswith("attachment;")
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in policy
        assert "sandbox" in policy
        assert (
            "script-src" not in policy
            or "'unsafe-inline'" not in policy.split("script-src")[1][:40]
        )

    async def test_a_report_is_never_cached_by_a_shared_cache(self, api_client, case_id):
        response = await api_client.get(f"/api/v1/cases/{case_id}/report")
        assert "no-store" in response.headers["Cache-Control"]
        assert "private" in response.headers["Cache-Control"]

    async def test_a_case_name_cannot_inject_a_header(self, api_client):
        """A filename is built from investigator-supplied text."""
        created = await api_client.post(
            "/api/v1/cases",
            json={"name": 'Bad"\r\nX-Injected: yes\r\nname'},
        )
        assert created.status_code == 201
        case = created.json()["id"]
        response = await api_client.get(f"/api/v1/cases/{case}/report")
        assert response.status_code == 200
        assert "X-Injected" not in response.headers
        disposition = response.headers["Content-Disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        assert disposition.count('"') == 2

    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "<svg/onload=alert(1)>",
            '"><script>alert(document.cookie)</script>',
            "<iframe src='javascript:alert(1)'></iframe>",
            "</textarea><script>alert(1)</script>",
        ],
    )
    async def test_investigator_text_is_escaped_in_the_rendered_report(self, api_client, payload):
        created = await api_client.post(
            "/api/v1/cases", json={"name": f"Case {payload}", "description": payload}
        )
        assert created.status_code == 201
        case = created.json()["id"]
        response = await api_client.get(f"/api/v1/cases/{case}/report?report_format=html")
        assert response.status_code == 200
        body = response.text
        # No tag the payload tried to open survives as markup. Asserted on the
        # opening bracket rather than on a substring like "onerror=", which is
        # harmless — and still present — inside escaped text.
        assert "<script" not in body
        assert "<iframe" not in body
        assert "<svg" not in body
        assert "<img src=x" not in body
        if "<" in payload:
            # The unescaped form never appears anywhere in the document.
            assert payload not in body
            assert "&lt;" in body

    async def test_a_report_is_refused_without_a_session(self, api_client, case_id):
        """Rendering reads the whole case, so it is behind authentication."""
        api_client.cookies.clear()
        response = await api_client.get(f"/api/v1/cases/{case_id}/report")
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"


class TestEvidenceHandling:
    async def test_evidence_is_not_served_from_a_predictable_static_url(self):
        """Nothing is mounted. Every byte goes through an authorized route."""
        from starlette.staticfiles import StaticFiles

        from app.main import create_app

        app = create_app()
        for route in app.routes:
            assert not isinstance(getattr(route, "app", None), StaticFiles), route

    async def test_the_evidence_directory_is_not_reachable_over_http(self, api_client):
        for path in (
            "/data/evidence/",
            "/evidence/",
            "/api/v1/evidence",
            "/static/evidence/x",
        ):
            response = await api_client.get(path)
            assert response.status_code in {404, 405}, (path, response.status_code)

    async def test_verifying_evidence_requires_a_case_the_caller_can_reach(
        self, api_client, case_id
    ):
        ours = await api_client.get(f"/api/v1/cases/{case_id}/evidence/verify")
        assert ours.status_code == 200
        import uuid

        theirs = await api_client.get(f"/api/v1/cases/{uuid.uuid4()}/evidence/verify")
        assert theirs.status_code == 404


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


class TestProductionConfigurationGate:
    def test_a_safe_production_configuration_starts(self):
        settings = Settings(
            environment="production",
            session_secret="a" * 48,
            cors_origins=["https://app.example.com"],
        )
        assert settings.production_problems() == []
        settings.require_safe_production()

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"session_secret": None}, "SESSION_SECRET"),
            ({"session_secret": "changeme"}, "SESSION_SECRET"),
            ({"session_secret": "short"}, "SESSION_SECRET"),
            ({"debug": True}, "DEBUG"),
            ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
            ({"cors_origins": ["http://localhost:3000"]}, "CORS_ORIGINS"),
            ({"cors_origins": ["http://app.example.com"]}, "CORS_ORIGINS"),
            ({"allow_private_networks": True}, "ALLOW_PRIVATE_NETWORKS"),
            ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE"),
            ({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED"),
        ],
    )
    def test_a_dangerous_production_setting_refuses_to_start(self, kwargs, expected):
        base = {
            "environment": "production",
            "session_secret": "a" * 48,
            "cors_origins": ["https://app.example.com"],
        }
        settings = Settings(**{**base, **kwargs})
        problems = settings.production_problems()
        assert any(expected in problem for problem in problems), problems

        with pytest.raises(UnsafeProductionConfig) as raised:
            settings.require_safe_production()
        assert expected in str(raised.value)

    def test_nothing_is_repaired_silently(self):
        """The setting keeps the dangerous value; the process refuses instead."""
        settings = Settings(
            environment="production",
            session_secret="a" * 48,
            cors_origins=["*"],
            debug=True,
        )
        with pytest.raises(UnsafeProductionConfig):
            settings.require_safe_production()
        assert settings.debug is True
        assert settings.cors_origins == ["*"]

    def test_every_problem_is_reported_at_once(self):
        """An operator should not fix these one restart at a time."""
        settings = Settings(
            environment="production",
            session_secret=None,
            debug=True,
            cors_origins=["*", "http://localhost:3000"],
            rate_limit_enabled=False,
        )
        assert len(settings.production_problems()) >= 4

    def test_a_message_names_the_variable_and_the_remedy(self):
        settings = Settings(environment="production", session_secret=None)
        text = "\n".join(settings.production_problems())
        assert "SESSION_SECRET" in text
        assert "secrets.token_urlsafe" in text

    def test_development_is_not_held_to_the_production_gate(self):
        """Otherwise nobody could run this locally."""
        settings = Settings(environment="development", debug=True, session_secret=None)
        assert settings.production_problems() == []
        settings.require_safe_production()

    def test_private_network_access_stays_off_by_default(self):
        assert Settings().allow_private_networks is False
        assert (
            Settings(environment="production", session_secret="a" * 48).allow_private_networks
            is False
        )

    def test_private_network_access_needs_a_written_authorization(self):
        authorized = Settings(
            environment="production",
            session_secret="a" * 48,
            cors_origins=["https://app.example.com"],
            allow_private_networks=True,
            private_network_authorization="Engagement PENTEST-2026-04",
        )
        assert authorized.production_problems() == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "https://a.example.com,https://b.example.com",
                ["https://a.example.com", "https://b.example.com"],
            ),
            ('["https://a.example.com"]', ["https://a.example.com"]),
            ("https://a.example.com", ["https://a.example.com"]),
        ],
    )
    def test_an_operator_can_write_the_origin_list_either_way(self, raw, expected, monkeypatch):
        """The comma form used to crash at startup with a JSON parse error."""
        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert Settings().cors_origins == expected


class TestSearchProviderRemainsOptional:
    async def test_no_provider_configured_is_a_supported_deployment(self, api_client, case_id):
        """The whole platform must still work with SEARCH_PROVIDER=none."""
        from app.core.settings import get_settings

        assert get_settings().search_provider == "none"

        created = await api_client.post(
            f"/api/v1/cases/{case_id}/targets",
            json={"value": "Ada Lovelace", "type": "PERSON"},
        )
        assert created.status_code == 201
        target_id = created.json()["id"]

        response = await api_client.post(f"/api/v1/cases/{case_id}/targets/{target_id}/search")
        assert response.status_code == 200
        body = response.json()
        # It says it was never searched, rather than reporting zero results.
        assert body["configured"] is False
        assert body["reason"]
        assert body["queries_run"] == 0

    async def test_the_rest_of_the_case_still_works_without_a_provider(self, api_client, case_id):
        for path in ("", "/summary", "/report", "/findings", "/entities", "/timeline"):
            response = await api_client.get(f"/api/v1/cases/{case_id}{path}")
            assert response.status_code == 200, (path, response.text)


# --------------------------------------------------------------------------
# The outbound guard, re-asserted
# --------------------------------------------------------------------------


class TestSsrfGuardNotWeakened:
    """A regression fence around the network path this PR did not change.

    Authorization was added *in front of* outbound fetching, and adding a new
    caller (an authenticated one) is exactly the kind of change that quietly
    routes around a guard. These re-assert the guard from the pilot's point of
    view: the vectors a reviewer will ask about, plus the settings that control
    them.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://127.0.0.1:8000/admin",
            "http://127.1/",
            "https://localhost/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://0.0.0.0/",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/",
            "http://10.0.0.5/",
            "http://172.16.31.9/",
            "http://192.168.1.1/",
            "http://[fd00::1]/",
            "http://[fe80::1]/",
        ],
    )
    def test_an_internal_destination_is_refused(self, url):
        from app.core.errors import SSRFError
        from app.core.ssrf import validate_url

        with pytest.raises(SSRFError):
            validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/secrets",
            "gopher://example.com/",
            "data:text/html,<script>alert(1)</script>",
            "jar:http://example.com!/",
            "//example.com/protocol-relative",
            "http://",
        ],
    )
    def test_a_non_http_scheme_is_refused(self, url):
        from app.core.errors import SSRFError
        from app.core.ssrf import validate_url

        with pytest.raises(SSRFError):
            validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:secret@example.com/",
            "https://user@example.com/",
            "https://example.com%20@127.0.0.1/",
        ],
    )
    def test_a_credential_in_the_authority_is_refused(self, url):
        """The classic ``https://trusted.example@127.0.0.1/`` confusion."""
        from app.core.errors import SSRFError
        from app.core.ssrf import validate_url

        with pytest.raises(SSRFError):
            validate_url(url)

    def test_a_name_that_resolves_inward_is_refused(self):
        """The guard rules on the resolved address, not on the name.

        This is the testable half of DNS rebinding: ``metadata.google.internal``
        is resolved by the suite's stub to 169.254.169.254, so a name that is not
        on any block-list still fails because of where it points. The other half —
        a name that answers differently on the second lookup — is not testable
        here and is handled by the connection being made to the address the guard
        resolved; that is stated in ``docs/threat-model.md``.
        """
        from app.core.errors import SSRFError
        from app.core.ssrf import validate_url

        with pytest.raises(SSRFError) as raised:
            validate_url("http://metadata.google.internal/computeMetadata/v1/")
        assert "blocked" in str(raised.value)

    def test_a_public_destination_is_still_allowed(self):
        """A guard that refused everything would pass the tests above for free."""
        from app.core.ssrf import validate_url

        target = validate_url("https://example.com/path")
        assert target.hostname == "example.com"

    def test_private_networks_remain_off_by_default(self):
        from app.core.settings import get_settings

        assert get_settings().allow_private_networks is False

    def test_report_and_evidence_paths_do_not_bypass_the_guard(self):
        """Every outbound fetch goes through one module, which validates first."""
        import pathlib
        import re

        root = pathlib.Path("app")
        offenders = []
        for path in root.rglob("*.py"):
            if path.parts[:2] == ("app", "core") and path.name in {"http.py", "ssrf.py"}:
                continue
            text = path.read_text(encoding="utf-8")
            # A direct client construction outside the guarded module would be a
            # second, unvalidated way out of the process.
            if re.search(r"httpx\.(Async)?Client\(", text):
                offenders.append(str(path))
        assert offenders == [], offenders
