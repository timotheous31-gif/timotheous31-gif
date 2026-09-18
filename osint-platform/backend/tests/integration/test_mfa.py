"""Two-factor authentication: the happy path, and every way around it I could find.

The bypass tests matter more than the happy path. A second factor that can be
skipped is worse than none, because it is believed. So each test below names the
specific way in that it closes.
"""

from __future__ import annotations

import pyotp
import pytest

from app.core import throttle
from app.core.db import get_session_factory
from app.models.auth import MfaRecoveryCode, UserMfa
from app.models.enums import AuditEvent
from tests.conftest import TEST_PASSWORD, _bootstrap_account

API = "/api/v1"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    backend = throttle.MemoryThrottle()
    throttle.set_throttle(backend)
    yield backend
    throttle.set_throttle(None)


def _audit(event: AuditEvent):
    from app.models.auth import AuditLogEntry

    with get_session_factory()() as session:
        return list(session.query(AuditLogEntry).filter(AuditLogEntry.event_type == event).all())


def _secret_of(email: str = "analyst@example.com") -> str:
    from app.models.auth import User

    with get_session_factory()() as session:
        user = session.query(User).filter(User.email == email).one()
        row = session.query(UserMfa).filter(UserMfa.user_id == user.id).one()
        return row.secret


def _code(secret: str, *, offset: int = 0) -> str:
    import time

    return pyotp.TOTP(secret).at(time.time() + offset * 30)


async def _enroll(client) -> tuple[str, list[str]]:
    """Take a signed-in client all the way through enrolment.

    Note for every caller below: confirming enrolment *spends* the code it was
    confirmed with, so a sign-in inside the same 30-second window has to use the
    next one. That is the replay guard doing its job — the same six digits really
    were already used — and :func:`_code` takes an ``offset`` for exactly this.
    """
    started = await client.post(f"{API}/auth/mfa/enroll", json={"password": TEST_PASSWORD})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(f"{API}/auth/mfa/confirm", json={"code": _code(secret)})
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


async def _sign_in(client, email: str = "analyst@example.com"):
    response = await client.post(
        f"{API}/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    if response.status_code == 200:
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


class TestEnrollment:
    async def test_the_happy_path(self, api_client):
        secret, codes = await _enroll(api_client)
        assert len(secret) >= 16
        assert len(codes) == 10

        status = await api_client.get(f"{API}/auth/mfa")
        body = status.json()
        assert body["enabled"] is True
        assert body["recovery_codes_remaining"] == 10

        # The ledger records how many codes were issued. Named `codes_issued`
        # rather than `recovery_codes_issued` because the metadata filter matches
        # `recovery_code` by substring and would drop the count with the code.
        (entry,) = _audit(AuditEvent.MFA_ENABLED)
        assert entry.metadata_["codes_issued"] == 10

    async def test_enrollment_is_not_active_until_a_code_confirms_it(self, api_client):
        """A scanned QR code that was never confirmed must not gate sign-in.

        Otherwise somebody who scanned into an app they then deleted is locked
        out by a factor they never proved they had.
        """
        started = await api_client.post(f"{API}/auth/mfa/enroll", json={"password": TEST_PASSWORD})
        assert started.status_code == 200
        assert started.json()["confirmed"] is False

        # The row exists, but the account is not gated.
        assert (await api_client.get(f"{API}/auth/mfa")).json()["enabled"] is False
        api_client.cookies.clear()
        again = await _sign_in(api_client)
        assert again.status_code == 200
        assert again.json()["mfa_required"] is False
        # And a fully-authenticated route works with no challenge.
        assert (await api_client.get(f"{API}/cases")).status_code == 200

    async def test_enrollment_requires_the_password(self, api_client):
        """An unlocked browser must not be enough to bind a new authenticator."""
        response = await api_client.post(
            f"{API}/auth/mfa/enroll", json={"password": "not the password"}
        )
        assert response.status_code == 401
        with get_session_factory()() as session:
            assert session.query(UserMfa).count() == 0

    async def test_confirmation_refuses_a_wrong_code(self, api_client):
        await api_client.post(f"{API}/auth/mfa/enroll", json={"password": TEST_PASSWORD})
        response = await api_client.post(f"{API}/auth/mfa/confirm", json={"code": "000000"})
        assert response.status_code == 422
        assert (await api_client.get(f"{API}/auth/mfa")).json()["enabled"] is False
        assert _audit(AuditEvent.MFA_CHALLENGE_FAILED)

    async def test_enrolling_twice_is_refused(self, api_client):
        await _enroll(api_client)
        again = await api_client.post(f"{API}/auth/mfa/enroll", json={"password": TEST_PASSWORD})
        assert again.status_code == 409


class TestLoginChallenge:
    async def test_password_alone_does_not_authenticate(self, api_client):
        """The central property. A password-stage session can reach nothing."""
        await _enroll(api_client)
        api_client.cookies.clear()

        signin = await _sign_in(api_client)
        assert signin.status_code == 200
        assert signin.json()["mfa_required"] is True

        for path in ("/cases", "/workspaces", "/auth/me", "/collectors", "/jobs"):
            response = await api_client.get(f"{API}{path}")
            assert response.status_code == 401, path
            assert response.json()["code"] == "mfa_required", path

    async def test_a_pending_session_cannot_write_either(self, api_client):
        await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        response = await api_client.post(f"{API}/cases", json={"name": "Snuck in"})
        assert response.status_code == 401
        assert response.json()["code"] == "mfa_required"

    async def test_the_code_completes_sign_in(self, api_client):
        secret, _ = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)

        verified = await api_client.post(
            f"{API}/auth/mfa/verify", json={"code": _code(secret, offset=1)}
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["mfa_required"] is False
        assert (await api_client.get(f"{API}/cases")).status_code == 200

    async def test_a_pending_session_may_always_sign_out(self, api_client):
        """Being half-signed-in must never be a trap."""
        await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        assert (await api_client.post(f"{API}/auth/logout")).status_code == 204

    async def test_a_replayed_code_is_refused(self, api_client):
        """A code seen once — over a shoulder, through a proxy — is spent."""
        secret, _ = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        code = _code(secret, offset=1)
        assert (
            await api_client.post(f"{API}/auth/mfa/verify", json={"code": code})
        ).status_code == 200

        # A second session, same code, still inside its window.
        api_client.cookies.clear()
        await _sign_in(api_client)
        replay = await api_client.post(f"{API}/auth/mfa/verify", json={"code": code})
        assert replay.status_code == 401
        assert (await api_client.get(f"{API}/cases")).status_code == 401

    async def test_a_wrong_code_is_refused_and_recorded(self, api_client):
        await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        response = await api_client.post(f"{API}/auth/mfa/verify", json={"code": "123456"})
        assert response.status_code == 401
        assert _audit(AuditEvent.MFA_CHALLENGE_FAILED)

    async def test_the_challenge_is_throttled(self, api_client):
        """Six digits is 10^6. An unlimited challenge is not a factor."""
        from app.core.settings import get_settings

        await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        object.__setattr__(get_settings(), "mfa_max_attempts", 3)

        codes = [
            (await api_client.post(f"{API}/auth/mfa/verify", json={"code": "000000"})).status_code
            for _ in range(6)
        ]
        assert 429 in codes, codes

    async def test_an_empty_challenge_is_refused(self, api_client):
        await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        response = await api_client.post(f"{API}/auth/mfa/verify", json={})
        assert response.status_code == 422


class TestRecoveryCodes:
    async def test_a_recovery_code_completes_sign_in_once(self, api_client):
        _, codes = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)

        first = await api_client.post(f"{API}/auth/mfa/verify", json={"recovery_code": codes[0]})
        assert first.status_code == 200
        assert (await api_client.get(f"{API}/cases")).status_code == 200
        (used,) = _audit(AuditEvent.MFA_RECOVERY_CODE_USED)
        assert used.metadata_["codes_remaining"] == 9

        # The same code, a second time.
        api_client.cookies.clear()
        await _sign_in(api_client)
        reused = await api_client.post(f"{API}/auth/mfa/verify", json={"recovery_code": codes[0]})
        assert reused.status_code == 401
        assert (await api_client.get(f"{API}/cases")).status_code == 401

    async def test_codes_are_stored_hashed_never_plaintext(self, api_client):
        _, codes = await _enroll(api_client)
        with get_session_factory()() as session:
            rows = session.query(MfaRecoveryCode).all()
        assert len(rows) == 10
        stored = " ".join(row.code_hash for row in rows)
        for code in codes:
            assert code not in stored
            assert code.replace("-", "") not in stored
        assert all(row.code_hash.startswith("$argon2id$") for row in rows)

    async def test_a_spent_code_is_kept_as_a_record(self, api_client):
        """ "Was a recovery code used, and when" needs an answer."""
        _, codes = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        await api_client.post(f"{API}/auth/mfa/verify", json={"recovery_code": codes[0]})

        with get_session_factory()() as session:
            spent = session.query(MfaRecoveryCode).filter(MfaRecoveryCode.used_at.isnot(None)).all()
        assert len(spent) == 1

    async def test_a_separator_or_case_difference_still_works(self, api_client):
        """Read off paper, typed by a human under stress."""
        _, codes = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        messy = codes[0].lower().replace("-", " ")
        assert (
            await api_client.post(f"{API}/auth/mfa/verify", json={"recovery_code": messy})
        ).status_code == 200

    async def test_another_accounts_code_does_not_work(self, api_client, anonymous_client):
        """A recovery code is bound to its account."""
        _, codes = await _enroll(api_client)
        _bootstrap_account(email="other@example.com", workspace="Other Firm")

        import httpx

        from app.main import create_app

        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
            signin = await other.post(
                f"{API}/auth/login",
                json={"email": "other@example.com", "password": TEST_PASSWORD},
            )
            other.headers["X-CSRF-Token"] = signin.json()["csrf_token"]
            # That account has no MFA, so it is already signed in; the point is
            # that the code cannot be spent against it.
            response = await other.post(f"{API}/auth/mfa/verify", json={"recovery_code": codes[0]})
        # Either way, the code must still be unspent on its own account.
        with get_session_factory()() as session:
            assert (
                session.query(MfaRecoveryCode).filter(MfaRecoveryCode.used_at.isnot(None)).count()
                == 0
            )
        assert response.status_code in {200, 401}


class TestDisabling:
    async def test_disabling_requires_the_password(self, api_client):
        await _enroll(api_client)
        response = await api_client.post(
            f"{API}/auth/mfa/disable", json={"password": "not the password"}
        )
        assert response.status_code == 401
        assert (await api_client.get(f"{API}/auth/mfa")).json()["enabled"] is True

    async def test_disabling_removes_the_factor_and_every_code(self, api_client):
        await _enroll(api_client)
        response = await api_client.post(
            f"{API}/auth/mfa/disable", json={"password": TEST_PASSWORD}
        )
        assert response.status_code == 204
        with get_session_factory()() as session:
            assert session.query(UserMfa).count() == 0
            assert session.query(MfaRecoveryCode).count() == 0
        assert _audit(AuditEvent.MFA_DISABLED)

    async def test_an_admin_password_reset_does_not_disable_mfa(self, api_client):
        """Shell access must not become account takeover.

        Resetting a password is a support action. If it silently cleared the
        second factor, anyone who could run the CLI could take an account whose
        phone they do not have.
        """
        from typer.testing import CliRunner

        from app.cli.admin import admin_app

        await _enroll(api_client)
        replacement = "a replacement passphrase"
        result = CliRunner().invoke(
            admin_app,
            ["reset-password", "--email", "analyst@example.com"],
            input=f"{replacement}\n{replacement}\n",
        )
        assert result.exit_code == 0, result.output

        with get_session_factory()() as session:
            assert session.query(UserMfa).count() == 1
            assert session.query(UserMfa).one().confirmed_at is not None
            assert session.query(MfaRecoveryCode).count() == 10

        # And signing in with the new password still owes the factor.
        api_client.cookies.clear()
        signin = await api_client.post(
            f"{API}/auth/login",
            json={"email": "analyst@example.com", "password": replacement},
        )
        assert signin.status_code == 200
        assert signin.json()["mfa_required"] is True

    async def test_the_reset_records_that_mfa_survived(self, api_client):
        from typer.testing import CliRunner

        from app.cli.admin import admin_app

        await _enroll(api_client)
        replacement = "a replacement passphrase"
        CliRunner().invoke(
            admin_app,
            ["reset-password", "--email", "analyst@example.com"],
            input=f"{replacement}\n{replacement}\n",
        )
        entries = _audit(AuditEvent.PASSWORD_CHANGED)
        assert entries
        assert entries[-1].metadata_["mfa_left_enabled"] is True
        assert entries[-1].metadata_["by"] == "admin-cli"


class TestSecretsNeverLeak:
    async def test_the_secret_is_returned_once_and_never_again(self, api_client):
        secret, _ = await _enroll(api_client)

        status = await api_client.get(f"{API}/auth/mfa")
        assert secret not in status.text
        me = await api_client.get(f"{API}/auth/me")
        assert secret not in me.text

        api_client.cookies.clear()
        signin = await _sign_in(api_client)
        assert secret not in signin.text

    async def test_no_secret_or_code_reaches_the_audit_ledger(self, api_client):
        from app.models.auth import AuditLogEntry

        secret, codes = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        await api_client.post(f"{API}/auth/mfa/verify", json={"code": "000000"})
        await api_client.post(f"{API}/auth/mfa/verify", json={"code": _code(secret, offset=1)})

        with get_session_factory()() as session:
            everything = " ".join(str(e.metadata_) for e in session.query(AuditLogEntry).all())
        assert secret not in everything
        for code in codes:
            assert code not in everything
            assert code.replace("-", "") not in everything

    def test_the_metadata_filter_drops_factor_material_by_key(self):
        """Belt and braces: even if a caller passed one, it would not land."""
        from app.services.audit import safe_metadata

        kept = safe_metadata(
            {
                "mfa_secret": "JBSWY3DPEHPK3PXP",
                "totp_secret": "JBSWY3DPEHPK3PXP",
                "recovery_code": "ABCDE-FGHIJ",
                "code_hash": "$argon2id$...",
                "stage": "login",
            }
        )
        assert "JBSWY3DPEHPK3PXP" not in str(kept)
        assert "ABCDE-FGHIJ" not in str(kept)
        assert kept["stage"] == "login"


class TestRegression:
    async def test_an_account_without_mfa_is_unaffected(self, api_client):
        """The default path must not change for anybody who has not enrolled."""
        api_client.cookies.clear()
        signin = await _sign_in(api_client)
        assert signin.status_code == 200
        assert signin.json()["mfa_required"] is False
        assert (await api_client.get(f"{API}/cases")).status_code == 200

    async def test_workspace_isolation_still_holds_behind_mfa(self, api_client):
        """A satisfied factor grants the account's own scope and nothing wider."""
        import uuid

        secret, _ = await _enroll(api_client)
        api_client.cookies.clear()
        await _sign_in(api_client)
        await api_client.post(f"{API}/auth/mfa/verify", json={"code": _code(secret, offset=1)})

        stranger = uuid.uuid4()
        assert (await api_client.get(f"{API}/cases/{stranger}")).status_code == 404
        assert (await api_client.get(f"{API}/workspaces/{stranger}")).status_code == 404
