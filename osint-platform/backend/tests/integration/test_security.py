"""Security guarantees, asserted end to end.

These tests exist to make the platform's promises falsifiable. Each one
corresponds to a claim made in the README and in the report's methodology
section; if a change breaks a promise, a test here fails rather than the
promise quietly becoming untrue.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core import http as http_module
from app.core.errors import PolicyError, SSRFError
from app.models.enums import Classification


class TestSSRF:
    """Outbound requests cannot be steered at internal infrastructure."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/admin",
            "http://localhost/",
            "http://10.0.0.1/",
            "http://192.168.0.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://metadata.google.internal/",
            "file:///etc/passwd",
            "gopher://example.com/",
        ],
    )
    async def test_internal_and_non_http_targets_are_refused(self, url):
        with pytest.raises(SSRFError):
            await http_module.get(url)

    @respx.mock
    async def test_a_redirect_cannot_smuggle_a_request_inward(self, mock_http):
        respx.get("https://example.com/redirect").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        )
        with pytest.raises(SSRFError):
            await http_module.get("https://example.com/redirect")

    async def test_credentials_in_a_url_are_refused(self):
        with pytest.raises(SSRFError):
            await http_module.get("https://user:secret@example.com/")


class TestResourceLimits:
    """A hostile or broken upstream cannot exhaust the process."""

    @respx.mock
    async def test_response_size_is_capped(self, mock_http):
        from app.core.errors import ResponseTooLarge

        respx.get("https://example.com/huge").mock(
            return_value=httpx.Response(200, content=b"x" * 200_000)
        )
        with pytest.raises(ResponseTooLarge):
            await http_module.get("https://example.com/huge", validate=False, max_bytes=1000)

    @respx.mock
    async def test_redirect_chains_are_bounded(self, mock_http):
        for index in range(12):
            respx.get(f"https://example.com/hop{index}").mock(
                return_value=httpx.Response(302, headers={"location": f"/hop{index + 1}"})
            )
        from app.core.errors import TooManyRedirects

        with pytest.raises(TooManyRedirects, match="redirects"):
            await http_module.get("https://example.com/hop0", validate=False, max_redirects=3)

    async def test_over_large_request_bodies_are_rejected(self, api_client):
        response = await api_client.post(
            "/api/v1/cases",
            content=b'{"name": "' + b"x" * 2_000_000 + b'"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "request_too_large"


class TestInjection:
    """Investigation data is never interpreted as code or as SQL."""

    async def test_sql_metacharacters_in_a_case_name_are_data(self, api_client):
        hostile = "Robert'); DROP TABLE cases;--"
        created = await api_client.post("/api/v1/cases", json={"name": hostile})
        assert created.status_code == 201
        assert created.json()["name"] == hostile

        # The table still exists and the row is retrievable by id.
        listing = await api_client.get("/api/v1/cases")
        assert listing.status_code == 200
        assert any(item["name"] == hostile for item in listing.json()["items"])

    async def test_sql_metacharacters_in_a_filter_are_data(self, api_client):
        response = await api_client.get("/api/v1/cases", params={"q": "%' OR '1'='1"})
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_html_in_collected_data_is_escaped_in_the_report(self, api_client, db_session):
        from app.models import Case, Finding
        from app.models.enums import FindingKind

        case = Case(name="XSS case")
        db_session.add(case)
        db_session.flush()
        db_session.add(
            Finding(
                case_id=case.id,
                kind=FindingKind.HTTP_METADATA,
                title="<script>alert('title')</script>",
                summary="<img src=x onerror=alert(1)>",
                data={"description": "<svg onload=alert(2)>"},
                collector="http_meta",
                confidence=0.9,
                dedupe_key="xss",
            )
        )
        db_session.commit()

        report = await api_client.get(f"/api/v1/cases/{case.id}/report")
        body = report.text
        assert "<script>alert('title')</script>" not in body
        assert "<img src=x" not in body
        assert "<svg onload" not in body
        assert "&lt;script&gt;" in body


class TestPrivacyGuarantees:
    """The platform does not store what it promises not to store."""

    def test_credentials_never_reach_the_database(self, db_session):
        from app.privacy.filter import PrivacyFilter

        # Built from parts rather than written as literals: a string matching a
        # real credential format trips secret scanners even when it is invented.
        stripe_key = "sk_" + "live_" + "abcdefghijklmnopqrstuvwx"
        aws_key = "AKIA" + "1234567890ABCDEF"

        outcome = PrivacyFilter().filter_finding(
            {
                "api_key": stripe_key,
                "note": f"aws key {aws_key}",
                "hostname": "example.com",
            }
        )
        serialized = str(outcome.data)
        assert stripe_key not in serialized
        assert aws_key not in serialized
        assert "example.com" in serialized
        assert outcome.classification is Classification.RESTRICTED

    def test_credentials_never_reach_the_evidence_directory(
        self, db_session, tmp_path, monkeypatch
    ):
        from app.core.settings import reset_settings_cache
        from app.models import Case
        from app.services.evidence import EvidenceStore

        monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
        reset_settings_cache()
        try:
            case = Case(name="Evidence case")
            db_session.add(case)
            db_session.flush()

            store = EvidenceStore()
            stored = store.store(
                db_session,
                case_id=case.id,
                collector="http_meta",
                source_url="https://example.com/.env",
                content={"body": "GITHUB_TOKEN=ghp_" + "a1" * 18},
            )
            written = store.read_raw(case.id, stored.sha256).decode()
            assert "ghp_" not in written
            assert stored.evidence.redacted is True
        finally:
            reset_settings_cache()

    def test_secrets_never_reach_the_logs(self):
        from app.core.logging import scrub_secrets

        event = scrub_secrets(
            None,
            "info",
            {
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
                "body": "token=ghp_" + "b" * 36,
                "url": "https://example.com/",
            },
        )
        serialized = str(event)
        assert "abcdefghijklmnopqrstuvwxyz" not in serialized
        assert "ghp_" not in serialized
        assert "https://example.com/" in serialized

    def test_settings_do_not_leak_credentials_in_their_repr(self):
        from app.core.settings import Settings

        settings = Settings(
            _env_file=None,
            github_token="ghp_realtoken000000000000000000000000",
            hibp_api_key="hibp-secret-value",
        )
        rendered = f"{settings!r} {settings}"
        assert "ghp_realtoken" not in rendered
        assert "hibp-secret-value" not in rendered

    async def test_the_api_never_returns_a_credential(self, api_client, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secrettoken00000000000000000000000")
        from app.core.settings import reset_settings_cache

        reset_settings_cache()
        try:
            for path in ("/api/v1/collectors", "/health", "/health/ready", "/openapi.json"):
                response = await api_client.get(path)
                assert "ghp_secrettoken" not in response.text, path
        finally:
            reset_settings_cache()


class TestCollectionPolicy:
    """Politeness and consent controls are enforced, not merely documented."""

    @respx.mock
    async def test_robots_disallow_stops_collection(self, collector_ctx, mock_http):
        from app.collectors.http_meta import HTTPMetadataCollector
        from app.services.normalization import normalize_target

        respx.get("https://example.com/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        )
        page = respx.get("https://example.com/").mock(return_value=httpx.Response(200))

        with pytest.raises(PolicyError):
            await HTTPMetadataCollector().collect(normalize_target("example.com"), collector_ctx)
        assert page.call_count == 0

    def test_no_collector_implements_a_prohibited_capability(self):
        """The prohibited-capability list is enforced against the source tree."""
        import ast
        import pathlib

        collectors_dir = pathlib.Path(__file__).resolve().parents[2] / "app" / "collectors"
        forbidden_imports = {"smtplib", "poplib", "imaplib", "ftplib", "telnetlib", "paramiko"}
        forbidden_url_terms = (
            "/login",
            "/signin",
            "/password",
            "/reset",
            "/recover",
            "/forgot",
            "/oauth/token",
        )

        for path in collectors_dir.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                    assert not names & forbidden_imports, f"{path.name} imports {names}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden_imports, f"{path.name} imports {root}"
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    lowered = node.value.lower()
                    if "://" not in lowered:
                        continue
                    for term in forbidden_url_terms:
                        assert term not in lowered, f"{path.name} references {term}"

    def test_username_confidence_can_never_assert_identity(self):
        """No number of handle matches may equal a self-published link."""
        from app.correlation.confidence import default_engine

        handles = default_engine.score(
            [default_engine.signal("same_unique_username") for _ in range(50)]
        )
        published = default_engine.score([default_engine.signal("site_links_profile")])
        assert handles.score < published.score
        assert not handles.auto_mergeable


class TestSecurityHeaders:
    async def test_responses_carry_hardening_headers(self, api_client):
        response = await api_client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"

    async def test_internal_errors_do_not_leak_details(self, monkeypatch):
        """An unexpected failure returns a generic envelope, never internals."""
        from app.core.db import configure_engine
        from app.main import create_app
        from app.models import Base
        from app.services import cases as case_service

        def explode(*args, **kwargs):
            raise RuntimeError("connection string postgresql://user:hunter2@db/osint")

        monkeypatch.setattr(case_service, "list_cases", explode)

        engine = configure_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        # raise_app_exceptions=False makes the transport behave like a real
        # server, which returns the handler's response instead of re-raising.
        transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/cases")

        assert response.status_code == 500
        assert "hunter2" not in response.text
        assert "postgresql://" not in response.text
        assert response.json()["message"] == "An internal error occurred"
