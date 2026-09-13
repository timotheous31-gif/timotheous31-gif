"""Sign-in, sessions, CSRF and the shape of a failure.

The properties here are the ones whose absence would be invisible in normal use:
a login form works either way, and only an adversarial test notices that it also
tells an attacker which addresses have accounts.
"""

from __future__ import annotations

import pytest

from app.core.db import get_session_factory
from tests.conftest import TEST_PASSWORD, _bootstrap_account, _sign_in

pytestmark = pytest.mark.anyio

LOGIN = "/api/v1/auth/login"


async def test_every_route_requires_a_session(anonymous_client):
    """The whole API, not a sample of it.

    Enumerated from the live application rather than hand-listed, so a route
    added later is covered by this test the day it appears — which is the only
    way a test like this stays true.
    """
    app = anonymous_client._transport.app
    public = {"/health", "/health/ready", "/api/v1/auth/login"}
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if path in public or path.startswith("/docs") or path == "/openapi.json":
            continue
        for method in operations:
            if method not in {"get", "post", "patch", "delete", "put"}:
                continue
            # Path parameters are filled with a syntactically valid UUID: the
            # point is that authentication is refused *before* anything is
            # resolved, so the id never needs to exist.
            concrete = path
            for name in (
                "case_id",
                "workspace_id",
                "job_id",
                "target_id",
                "entity_id",
                "image_id",
                "membership_id",
                "subject_id",
            ):
                concrete = concrete.replace(
                    "{" + name + "}", "00000000-0000-4000-8000-000000000000"
                )
            concrete = concrete.replace("{subject_type}", "CANDIDATE")
            response = await anonymous_client.request(method.upper(), concrete, json={})
            assert response.status_code == 401, f"{method.upper()} {path} -> {response.status_code}"
            assert response.json()["code"] == "authentication_required"
            checked += 1
    assert checked > 40, f"only {checked} routes checked; the enumeration is wrong"


async def test_health_stays_anonymous(anonymous_client):
    """A liveness probe that needs a password is a liveness probe that fails."""
    assert (await anonymous_client.get("/health")).status_code == 200
    assert (await anonymous_client.get("/health/ready")).status_code in (200, 503)


@pytest.mark.parametrize(
    "email, password",
    [
        ("analyst@example.com", "the wrong passphrase entirely"),
        ("nobody-at-all@example.com", TEST_PASSWORD),
        ("nobody-at-all@example.com", "the wrong passphrase entirely"),
    ],
)
async def test_every_sign_in_failure_looks_identical(anonymous_client, email, password):
    """An unknown address and a wrong password must be indistinguishable.

    Otherwise the login form is an oracle for "does this person have an account
    with this customer", which is itself disclosure.
    """
    _bootstrap_account()
    response = await anonymous_client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["message"] == "Those credentials are not valid."
    assert response.json()["code"] == "authentication_required"


async def test_a_deactivated_account_cannot_sign_in_and_says_no_more(anonymous_client):
    _bootstrap_account()
    from app.models.auth import User

    with get_session_factory()() as session:
        user = session.query(User).filter(User.email == "analyst@example.com").one()
        user.is_active = False
        session.commit()

    response = await anonymous_client.post(
        LOGIN, json={"email": "analyst@example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["message"] == "Those credentials are not valid."


async def test_the_session_cookie_is_httponly_and_the_csrf_cookie_is_not(anonymous_client):
    """The split that makes an XSS survivable.

    The session must be unreadable by script; the CSRF token must be readable,
    because the page has to copy it into a header. Getting these the wrong way
    round would be a quiet catastrophe.
    """
    _bootstrap_account()
    response = await anonymous_client.post(
        LOGIN, json={"email": "analyst@example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.startswith("osint_session="))
    csrf_cookie = next(item for item in cookies if item.startswith("osint_csrf="))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=lax" in session_cookie.lower() or "samesite=lax" in session_cookie.lower()


async def test_no_credential_appears_in_the_sign_in_response(anonymous_client):
    _bootstrap_account()
    response = await anonymous_client.post(
        LOGIN, json={"email": "analyst@example.com", "password": TEST_PASSWORD}
    )
    body = response.text
    assert TEST_PASSWORD not in body
    assert "password" not in body.lower()
    assert "argon2" not in body.lower()
    # The session token is in the cookie, never in the body.
    assert response.json().get("csrf_token")
    assert "osint_session" not in body


async def test_a_state_changing_request_without_a_csrf_token_is_refused(api_client):
    """SameSite alone is explicitly not treated as sufficient."""
    del api_client.headers["X-CSRF-Token"]
    response = await api_client.post("/api/v1/cases", json={"name": "No token"})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def test_a_csrf_token_from_another_session_is_refused(anonymous_client):
    """The token is bound to one session, so a stolen one is worth nothing."""
    _bootstrap_account(email="one@example.com", workspace="One")
    _bootstrap_account(email="two@example.com", workspace="Two")

    other = await anonymous_client.post(
        LOGIN, json={"email": "two@example.com", "password": TEST_PASSWORD}
    )
    foreign_token = other.json()["csrf_token"]
    anonymous_client.cookies.clear()

    await _sign_in(anonymous_client, "one@example.com")
    response = await anonymous_client.post(
        "/api/v1/cases", json={"name": "Borrowed token"}, headers={"X-CSRF-Token": foreign_token}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def test_a_safe_method_needs_no_csrf_token(api_client):
    del api_client.headers["X-CSRF-Token"]
    assert (await api_client.get("/api/v1/cases")).status_code == 200


async def test_logout_revokes_the_session_server_side(anonymous_client):
    """Not merely clearing the cookie: a copied token must stop working too."""
    _bootstrap_account()
    token = await _sign_in(anonymous_client)
    stolen = anonymous_client.cookies.get("osint_session")

    assert (await anonymous_client.post("/api/v1/auth/logout")).status_code == 204

    # Replay the token an attacker would have captured before the logout.
    anonymous_client.cookies.set("osint_session", stolen)
    anonymous_client.headers["X-CSRF-Token"] = token
    assert (await anonymous_client.get("/api/v1/cases")).status_code == 401


async def test_an_expired_session_is_refused(anonymous_client):
    from datetime import UTC, datetime, timedelta

    from app.models.auth import UserSession

    _bootstrap_account()
    await _sign_in(anonymous_client)
    with get_session_factory()() as session:
        row = session.query(UserSession).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert (await anonymous_client.get("/api/v1/cases")).status_code == 401


async def test_an_idle_session_is_refused_and_revoked(anonymous_client):
    """Absolute lifetime and idle timeout are separate conditions."""
    from datetime import UTC, datetime, timedelta

    from app.models.auth import UserSession

    _bootstrap_account()
    await _sign_in(anonymous_client)
    with get_session_factory()() as session:
        row = session.query(UserSession).one()
        row.last_seen_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

    assert (await anonymous_client.get("/api/v1/cases")).status_code == 401
    with get_session_factory()() as session:
        assert session.query(UserSession).one().revoked_at is not None


async def test_changing_a_password_ends_every_session(anonymous_client):
    _bootstrap_account()
    await _sign_in(anonymous_client)
    response = await anonymous_client.post(
        "/api/v1/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "a different passphrase"},
    )
    assert response.status_code == 204
    assert (await anonymous_client.get("/api/v1/cases")).status_code == 401


async def test_changing_a_password_requires_the_current_one(api_client):
    """What stops an unlocked, walked-up-to browser becoming a takeover."""
    response = await api_client.post(
        "/api/v1/auth/password",
        json={"current_password": "not the right one", "new_password": "a different passphrase"},
    )
    assert response.status_code == 401


async def test_a_weak_new_password_is_refused_with_a_useful_reason(api_client):
    response = await api_client.post(
        "/api/v1/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "short"},
    )
    assert response.status_code == 422


async def test_the_stored_password_is_an_argon2id_verifier(db_session):
    """Never a plaintext password, never a reversible hash."""
    from app.models.auth import User
    from app.services import accounts

    accounts.create_user(db_session, email="hash@example.com", password=TEST_PASSWORD)
    db_session.commit()
    user = db_session.query(User).filter(User.email == "hash@example.com").one()
    assert user.password_hash.startswith("$argon2id$")
    assert TEST_PASSWORD not in user.password_hash


async def test_the_session_token_is_never_stored(db_session):
    """A stolen database must not yield a replayable session."""
    import hashlib

    from app.models.auth import UserSession
    from app.services import accounts

    user = accounts.create_user(db_session, email="tok@example.com", password=TEST_PASSWORD)
    _row, token = accounts.create_session(db_session, user=user)
    db_session.commit()

    stored = db_session.query(UserSession).one()
    assert stored.token_hash != token
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(stored.__dict__)
