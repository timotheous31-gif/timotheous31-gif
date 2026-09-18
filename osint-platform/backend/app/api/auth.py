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

from app.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    PendingUser,
    Principal,
    enforce_csrf,
)
from app.core import throttle
from app.core.errors import (
    AuthenticationRequired,
    ConflictError,
    NotFoundError,
    ThrottledError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.permissions import permissions_for
from app.core.security import (
    MfaError,
    PasswordPolicyError,
    csrf_token,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
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
from app.schemas.mfa import (
    MfaChallenge,
    MfaConfirm,
    MfaEnabledRead,
    MfaEnrollmentRead,
    MfaStatusRead,
    PasswordConfirmation,
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


def _session_info(
    session, user, *, csrf: str, expires_at: datetime, mfa_required: bool = False
) -> SessionInfo:
    memberships = accounts.memberships_for_user(session, user.id)
    return SessionInfo(
        mfa_required=mfa_required,
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
    pending = row.mfa_pending
    audit.record(
        session,
        event=AuditEvent.USER_LOGIN_SUCCESS,
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
        # A password-stage session is not a completed sign-in. Recording the
        # stage keeps "who got in" answerable: a PASSWORD entry with no matching
        # MFA one is somebody who had the password and not the phone.
        metadata={"stage": "password", "mfa_required": pending},
    )
    session.commit()
    _set_session_cookies(response, token=token, csrf=csrf, settings=settings)
    return _session_info(session, user, csrf=csrf, expires_at=row.expires_at, mfa_required=pending)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def logout(
    request: Request,
    response: Response,
    principal: PendingUser,
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
    audit.record(
        session,
        event=AuditEvent.PASSWORD_CHANGED,
        actor_user_id=principal.user_id,
        object_type="user",
        object_id=principal.user_id,
        # Neither password goes near this. `safe_metadata` would drop them by
        # key, and they are not put in front of it.
        metadata={"by": "self", "sessions_revoked": True},
    )
    session.commit()
    _clear_session_cookies(response, settings)


# ------------------------------------------------------------------- factors
#
# Five routes, and the invariants they exist to hold:
#
# * enrolment writes a secret but changes nothing about sign-in until a code
#   proves the authenticator holds the same secret;
# * turning the factor on or off requires the password again, because an
#   unlocked browser is the threat this factor exists to survive;
# * the challenge is throttled, because six digits is a small space;
# * a recovery code works once;
# * the secret is returned by exactly one route, once.


def _mfa_throttle_key(user_id) -> str:
    return throttle.principal_key("mfa:verify", str(user_id))


def _reauthenticate(principal: Principal, password: str) -> None:
    """Require the account's password again, or refuse."""
    if not verify_password(password, principal.user.password_hash):
        log.info("mfa.reauthentication_failed")
        raise AuthenticationRequired("Your password is not correct")


@router.get("/mfa", response_model=MfaStatusRead, summary="Two-factor status")
def mfa_status(principal: CurrentUser, session: DbSession) -> MfaStatusRead:
    """Whether a second factor is active. Carries no secret material."""
    row = accounts.mfa_for(session, principal.user_id)
    remaining = len(accounts.unused_recovery_codes(session, principal.user_id))
    return MfaStatusRead(
        enabled=row is not None and row.is_active,
        confirmed_at=row.confirmed_at if row is not None else None,
        recovery_codes_remaining=remaining,
    )


@router.post(
    "/mfa/enroll",
    response_model=MfaEnrollmentRead,
    summary="Begin two-factor enrolment",
)
def enroll_mfa(
    payload: PasswordConfirmation,
    request: Request,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MfaEnrollmentRead:
    """Generate a secret to scan. Nothing about sign-in changes yet.

    Deliberately does **not** activate the factor. A user who scans this into an
    app and then loses the phone before confirming still signs in with their
    password; a factor that started gating sign-in here would lock them out of
    their own account with something they never proved they had.
    """
    enforce_csrf(request, principal, settings)
    _reauthenticate(principal, payload.password)

    existing = accounts.mfa_for(session, principal.user_id)
    if existing is not None and existing.is_active:
        raise ConflictError(
            "Two-factor authentication is already enabled. Disable it first if you "
            "want to enrol a different authenticator."
        )

    row, secret = accounts.begin_mfa_enrollment(session, principal.user)
    session.commit()
    # Not audited: nothing has changed about the account's security yet, and an
    # entry here would read as "MFA was set up" for an enrolment that may be
    # abandoned. MFA_ENABLED is written when it becomes true.
    return MfaEnrollmentRead(
        secret=secret,
        otpauth_uri=totp_provisioning_uri(
            secret, account=principal.user.email, issuer=settings.app_name
        ),
        confirmed=row.is_active,
    )


@router.post(
    "/mfa/confirm",
    response_model=MfaEnabledRead,
    summary="Confirm enrolment and receive recovery codes",
)
def confirm_mfa(
    payload: MfaConfirm,
    request: Request,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MfaEnabledRead:
    """Prove the authenticator holds the secret, and turn the factor on."""
    enforce_csrf(request, principal, settings)
    row = accounts.mfa_for(session, principal.user_id)
    if row is None:
        raise ValidationError("Start enrolment before confirming it.")
    if row.is_active:
        raise ConflictError("Two-factor authentication is already enabled.")

    verdict = throttle.check(
        _mfa_throttle_key(principal.user_id),
        limit=settings.mfa_max_attempts,
        window_seconds=settings.mfa_attempt_window_seconds,
        settings=settings,
    )
    if verdict.refused:
        raise ThrottledError(
            "Too many codes tried. Wait a moment and try again.",
            retry_after=verdict.retry_after,
        )

    try:
        step = verify_totp(row.secret, payload.code, last_used_step=row.last_used_step)
    except MfaError as exc:
        audit.record(
            session,
            event=AuditEvent.MFA_CHALLENGE_FAILED,
            actor_user_id=principal.user_id,
            object_type="user",
            object_id=principal.user_id,
            metadata={"stage": "enrollment"},
        )
        session.commit()
        raise ValidationError(str(exc)) from exc

    codes = accounts.confirm_mfa_enrollment(session, row, step)
    throttle.forget(_mfa_throttle_key(principal.user_id), settings=settings)
    # This session proved the factor just now, so it is not sent back to a
    # challenge it has already passed.
    accounts.satisfy_mfa(session, principal.session)
    audit.record(
        session,
        event=AuditEvent.MFA_ENABLED,
        actor_user_id=principal.user_id,
        object_type="user",
        object_id=principal.user_id,
        metadata={"codes_issued": len(codes)},
    )
    session.commit()
    return MfaEnabledRead(enabled=True, recovery_codes=codes)


@router.post(
    "/mfa/verify",
    response_model=SessionInfo,
    summary="Answer the sign-in challenge",
)
def verify_mfa(
    payload: MfaChallenge,
    request: Request,
    principal: PendingUser,
    session: DbSession,
    settings: AppSettings,
) -> SessionInfo:
    """Complete sign-in for a session that has passed the password stage.

    Reached with a session that can do nothing else. Throttled per account:
    six digits is 10^6, and a challenge that could be retried without limit
    would be a factor in name only.
    """
    enforce_csrf(request, principal, settings)
    if not principal.session.mfa_pending:
        # Already satisfied. Not an error — a double-submitted form should not
        # look like a failure — but nothing is re-verified.
        csrf = csrf_token(str(principal.session.id), settings.session_secret_value())
        return _session_info(
            session, principal.user, csrf=csrf, expires_at=principal.session.expires_at
        )

    key = _mfa_throttle_key(principal.user_id)
    standing = throttle.peek(key, limit=settings.mfa_max_attempts, settings=settings)
    if standing.refused:
        audit.record(
            session,
            event=AuditEvent.RATE_LIMIT_TRIGGERED,
            actor_user_id=principal.user_id,
            object_type="mfa",
            object_id=principal.user_id,
            metadata={"reason": "too_many_mfa_attempts"},
        )
        session.commit()
        raise ThrottledError(
            "Too many codes tried. Wait a few minutes and try again.",
            retry_after=max(standing.retry_after, settings.mfa_lockout_seconds),
        )

    row = accounts.mfa_for(session, principal.user_id)
    if row is None or not row.is_active:
        # Nothing to verify. Rather than leaving the session stuck forever,
        # promote it: the account owes no factor.
        accounts.satisfy_mfa(session, principal.session)
        session.commit()
        csrf = csrf_token(str(principal.session.id), settings.session_secret_value())
        return _session_info(
            session, principal.user, csrf=csrf, expires_at=principal.session.expires_at
        )

    used_recovery = False
    if payload.recovery_code:
        used_recovery = accounts.consume_recovery_code(
            session, principal.user_id, payload.recovery_code
        )
        accepted = used_recovery
    elif payload.code:
        try:
            step = verify_totp(row.secret, payload.code, last_used_step=row.last_used_step)
        except MfaError:
            accepted = False
        else:
            row.last_used_step = step
            row.last_verified_at = datetime.now(UTC)
            accepted = True
    else:
        raise ValidationError("Supply an authenticator code or a recovery code.")

    if not accepted:
        throttle.check(
            key,
            limit=settings.mfa_max_attempts,
            window_seconds=settings.mfa_attempt_window_seconds,
            settings=settings,
        )
        audit.record(
            session,
            event=AuditEvent.MFA_CHALLENGE_FAILED,
            actor_user_id=principal.user_id,
            object_type="user",
            object_id=principal.user_id,
            # Never the code that was tried, nor the secret it was checked
            # against: the ledger records that a challenge failed, not what was
            # guessed.
            metadata={"stage": "login", "method": "recovery" if payload.recovery_code else "totp"},
        )
        session.commit()
        log.info("mfa.challenge_failed")
        raise AuthenticationRequired("That code is not valid.")

    throttle.forget(key, settings=settings)
    accounts.satisfy_mfa(session, principal.session)
    if used_recovery:
        remaining = len(accounts.unused_recovery_codes(session, principal.user_id))
        audit.record(
            session,
            event=AuditEvent.MFA_RECOVERY_CODE_USED,
            actor_user_id=principal.user_id,
            object_type="user",
            object_id=principal.user_id,
            metadata={"codes_remaining": remaining},
        )
    audit.record(
        session,
        event=AuditEvent.USER_LOGIN_SUCCESS,
        actor_user_id=principal.user_id,
        object_type="user",
        object_id=principal.user_id,
        metadata={"stage": "mfa", "method": "recovery" if used_recovery else "totp"},
    )
    session.commit()
    csrf = csrf_token(str(principal.session.id), settings.session_secret_value())
    return _session_info(
        session, principal.user, csrf=csrf, expires_at=principal.session.expires_at
    )


@router.post(
    "/mfa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Turn two-factor authentication off",
)
def disable_mfa(
    payload: PasswordConfirmation,
    request: Request,
    principal: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Remove the factor and every recovery code, after re-authentication."""
    enforce_csrf(request, principal, settings)
    _reauthenticate(principal, payload.password)

    existed = accounts.disable_mfa(session, principal.user_id)
    if not existed:
        raise NotFoundError("Two-factor authentication is not enabled on this account.")
    audit.record(
        session,
        event=AuditEvent.MFA_DISABLED,
        actor_user_id=principal.user_id,
        object_type="user",
        object_id=principal.user_id,
        metadata={"by": "self"},
    )
    session.commit()
