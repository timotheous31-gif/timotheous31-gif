"""Sign in, sign out, and "who am I".

The session is a cookie the page cannot read, plus a CSRF token the page must
echo. That split is the whole design:

* the **session cookie** is ``HttpOnly``, so a cross-site-scripting bug in the
  frontend cannot exfiltrate it;
* the **CSRF token** is readable by the page precisely because it must be put in
  a header, and it is worth nothing without the cookie.

Storing the session in ``localStorage`` instead would make one XSS equal to a
full account takeover, which is the trade this avoids.

Sign-in is the one route an unauthenticated caller may reach, so it carries the
throttling: per account and per client address, with a bounded cooldown that
cannot be used to lock somebody out permanently.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from app.api.deps import AppSettings, CurrentUser, DbSession, enforce_csrf
from app.core import throttle
from app.core.errors import AuthenticationRequired, ThrottledError, ValidationError
from app.core.logging import get_logger
from app.core.permissions import permissions_for
from app.core.security import PasswordPolicyError, csrf_token, verify_password
from app.core.settings import Settings
from app.models.enums import AuditEvent
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    SessionInfo,
    UserRead,
    WorkspaceRead,
    WorkspaceSummary,
)
from app.services import accounts, audit

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])

#: One sentence for every sign-in failure. An unknown address, a wrong password
#: and a deactivated account are indistinguishable — in wording, in status code,
#: and (see :func:`app.services.accounts.authenticate`) in how long they take.
LOGIN_FAILED = "Those credentials are not valid."


def _client_address(request: Request) -> str:
    """The caller's address, for throttling only.

    ``X-Forwarded-For`` is honoured because a pilot deployment sits behind a
    reverse proxy and the socket address would otherwise be the proxy for every
    caller — one shared bucket, which is the same as no per-address limit at all.
    It is trusted *only* for this purpose: the value never reaches the database,
    never reaches an audit entry, and is hashed before it becomes a throttle key.
    A spoofed header therefore buys an attacker their own bucket and nothing else.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _set_session_cookies(response: Response, *, token: str, csrf: str, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_lifetime_hours * 3600,
        httponly=True,
        secure=settings.cookies_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        max_age=settings.session_lifetime_hours * 3600,
        # Deliberately readable: the page has to copy it into a header. It
        # authorises nothing on its own.
        httponly=False,
        secure=settings.cookies_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            name,
            domain=settings.session_cookie_domain,
            path="/",
            secure=settings.cookies_secure,
            samesite=settings.session_cookie_samesite,
        )


def _session_info(session, user, *, csrf: str, expires_at: datetime) -> SessionInfo:
    memberships = accounts.memberships_for_user(session, user.id)
    return SessionInfo(
        user=UserRead.model_validate(user),
        workspaces=[
            WorkspaceSummary(
                workspace=WorkspaceRead.model_validate(membership.workspace),
                role=membership.role,
                permissions=permissions_for(membership.role),
            )
            for membership in memberships
        ],
        csrf_token=csrf,
        expires_at=expires_at,
    )


@router.post("/login", response_model=SessionInfo, summary="Sign in")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> SessionInfo:
    """Exchange credentials for a session cookie.

    Throttled on two keys at once — the account and the client address — because
    each alone leaves a gap: per-account only lets one attacker spray many
    accounts from one host, and per-address only lets a botnet grind one account.
    Both cooldowns expire on their own, so neither can be used to lock a real user
    out for good.
    """
    address = _client_address(request)
    account_key = throttle.principal_key("login:account", payload.email)
    address_key = throttle.principal_key("login:address", address)

    for key in (account_key, address_key):
        standing = throttle.peek(key, limit=settings.login_max_attempts, settings=settings)
        if standing.refused:
            audit.record(
                session,
                event=AuditEvent.RATE_LIMIT_TRIGGERED,
                object_type="login",
                metadata={"reason": "too_many_attempts"},
            )
            session.commit()
            raise ThrottledError(
                "Too many sign-in attempts. Wait a few minutes and try again.",
                retry_after=max(standing.retry_after, settings.login_lockout_seconds),
            )

    user = accounts.authenticate(session, email=payload.email, password=payload.password)
    if user is None:
        for key in (account_key, address_key):
            throttle.check(
                key,
                limit=settings.login_max_attempts,
                window_seconds=settings.login_attempt_window_seconds,
                settings=settings,
            )
        audit.record(
            session,
            event=AuditEvent.USER_LOGIN_FAILURE,
            object_type="login",
            # Never the address that was tried: a failure log that records every
            # attempted email becomes a list of who *might* have an account here.
            metadata={"outcome": "invalid_credentials"},
        )
        session.commit()
        log.info("auth.login_failed")
        raise AuthenticationRequired(LOGIN_FAILED)

    # A correct password clears the counters: one forgotten password should not
    # leave a real user rationed for the next fifteen minutes.
    throttle.forget(account_key, settings=settings)
    throttle.forget(address_key, settings=settings)

    row, token = accounts.create_session(
        session,
        user=user,
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    csrf = csrf_token(str(row.id), settings.session_secret_value())
    audit.record(
        session,
        event=AuditEvent.USER_LOGIN_SUCCESS,
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
    )
    session.commit()
    _set_session_cookies(response, token=token, csrf=csrf, settings=settings)
    return _session_info(session, user, csrf=csrf, expires_at=row.expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def logout(
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """End the session on the server, then clear the cookies.

    Server-side first, deliberately. Clearing a cookie only asks the browser to
    forget a token; revoking the row is what stops a copy of that token — taken
    from a shared machine, a proxy log, a stolen backup — from continuing to work.

    CSRF-protected like any other state-changing request: a forced logout is only
    a nuisance, but exempting a route because its damage is small is how the
    exemption list grows.
    """
    enforce_csrf(request, principal, settings)
    accounts.revoke_session(session, principal.session)
    audit.record(
        session,
        event=AuditEvent.USER_LOGOUT,
        actor_user_id=principal.user_id,
        object_type="user",
        object_id=principal.user_id,
    )
    session.commit()
    _clear_session_cookies(response, settings)


@router.get("/me", response_model=SessionInfo, summary="The signed-in user")
def me(principal: CurrentUser, session: DbSession, settings: AppSettings) -> SessionInfo:
    """Who the caller is, which workspaces they belong to, and at what role.

    Also returns a fresh CSRF token, so a page reloaded after a restart can
    recover one without making the user sign in again.
    """
    csrf = csrf_token(str(principal.session.id), settings.session_secret_value())
    expires = principal.session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return _session_info(session, principal.user, csrf=csrf, expires_at=expires)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your own password",
)
def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Change the caller's own password, ending every session they hold.

    The current password is required even though the caller is already signed in:
    it is what stops a walked-up-to, unlocked browser from becoming a permanent
    account takeover. And every session is revoked afterwards, including this one,
    because a password change that leaves old sessions alive does not evict
    whoever the change was made because of.
    """
    enforce_csrf(request, principal, settings)
    if not verify_password(payload.current_password, principal.user.password_hash):
        raise AuthenticationRequired("Your current password is not correct")
    try:
        accounts.set_password(session, principal.user, payload.new_password)
    except PasswordPolicyError as exc:
        raise ValidationError(str(exc)) from exc
    session.commit()
    _clear_session_cookies(response, settings)
