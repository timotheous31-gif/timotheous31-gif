"""Users, workspaces, memberships and sessions — the service layer.

Every function here takes a session and returns models; nothing knows about HTTP.
That separation is what lets the CLI create the first administrator through
exactly the same code path the API would, so there is one definition of "a valid
user" rather than two that drift.

The rules this module owns:

**A workspace always has an owner.** :func:`create_workspace` takes the owner as
an argument rather than allowing an ownerless workspace to exist for a moment,
and :func:`remove_member` and :func:`change_role` both refuse to remove the last
one. A workspace nobody can administer is a support ticket that cannot be closed.

**Sign-in tells an attacker nothing.** :func:`authenticate` returns the same
failure for an unknown address, a wrong password and a deactivated account, and
:func:`app.core.security.verify_password` spends the same Argon2 work in all
three cases.

**Logout ends the session server-side.** Revocation is a row update, not a
discarded cookie, so a stolen token stops working the moment its owner signs out.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    hash_password,
    hash_recovery_code,
    needs_rehash,
    new_recovery_codes,
    new_session_token,
    new_totp_secret,
    token_digest,
    verify_password,
    verify_recovery_code,
)
from app.core.settings import Settings, get_settings
from app.models.auth import (
    MfaRecoveryCode,
    User,
    UserMfa,
    UserSession,
    Workspace,
    WorkspaceMembership,
)
from app.models.case import Case
from app.models.enums import WorkspaceRole

log = get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
#: Rough shape check only. The authoritative validation is pydantic's EmailStr on
#: the request schema; this guards the CLI path, which has no schema in front of
#: it.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    """Lowercase and trim. The stored form and the lookup form are the same."""
    return (email or "").strip().lower()


def slugify(name: str) -> str:
    """A URL-safe handle for a workspace name."""
    slug = _SLUG_STRIP.sub("-", (name or "").strip().lower()).strip("-")
    return slug[:80] or "workspace"


# --------------------------------------------------------------------- users


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str = "",
) -> User:
    """Create an account. The password is hashed here and never stored."""
    address = normalize_email(email)
    if not _EMAIL_SHAPE.match(address):
        raise ValidationError(f"{email!r} is not a valid email address")
    if get_user_by_email(session, address) is not None:
        raise ConflictError(f"An account already exists for {address}")
    user = User(
        email=address,
        password_hash=hash_password(password),
        display_name=(display_name or address.split("@")[0])[:200],
    )
    session.add(user)
    session.flush()
    log.info("account.created", user_id=str(user.id))
    return user


def set_password(session: Session, user: User, password: str) -> None:
    """Replace a user's verifier and revoke every session they hold.

    Revoking is the point: a password change that leaves old sessions alive does
    not evict whoever the change was made because of.
    """
    user.password_hash = hash_password(password)
    revoke_all_sessions(session, user.id)
    session.flush()


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    """Return the user, or ``None`` — indistinguishably, for every failure.

    Three different failures share one answer and one cost: no such address, wrong
    password, deactivated account. The Argon2 verification runs even when there is
    no account, so latency does not disclose which case applied.
    """
    user = get_user_by_email(session, email)
    stored = user.password_hash if user is not None else None
    matched = verify_password(password, stored)
    if user is None or not matched or not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        # Free upgrade to current parameters, on a request that already has the
        # plaintext in hand. No password reset needed.
        user.password_hash = hash_password(password)
        session.flush()
    return user


# ---------------------------------------------------------------- workspaces


def create_workspace(
    session: Session,
    *,
    name: str,
    owner: User,
    slug: str | None = None,
) -> Workspace:
    """Create a workspace with ``owner`` as its OWNER, atomically."""
    handle = slugify(slug or name)
    if session.scalar(select(Workspace).where(Workspace.slug == handle)) is not None:
        raise ConflictError(f"A workspace already exists with the handle {handle!r}")
    workspace = Workspace(name=(name or handle)[:200], slug=handle)
    session.add(workspace)
    session.flush()
    add_member(session, workspace=workspace, user=owner, role=WorkspaceRole.OWNER)
    log.info("workspace.created", workspace_id=str(workspace.id), slug=handle)
    return workspace


def get_workspace_by_slug(session: Session, slug: str) -> Workspace | None:
    return session.scalar(select(Workspace).where(Workspace.slug == slug.strip().lower()))


def membership_for(
    session: Session, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> WorkspaceMembership | None:
    """The one membership row linking a user to a workspace, if any."""
    return session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )


def memberships_for_user(session: Session, user_id: uuid.UUID) -> list[WorkspaceMembership]:
    return list(
        session.scalars(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user_id)
            .order_by(WorkspaceMembership.created_at)
        )
    )


def add_member(
    session: Session, *, workspace: Workspace, user: User, role: WorkspaceRole
) -> WorkspaceMembership:
    existing = membership_for(session, user_id=user.id, workspace_id=workspace.id)
    if existing is not None:
        raise ConflictError("That user is already a member of this workspace")
    membership = WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role=role)
    session.add(membership)
    session.flush()
    return membership


def _owner_count(session: Session, workspace_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == WorkspaceRole.OWNER,
            )
        )
        or 0
    )


def change_role(
    session: Session, *, membership: WorkspaceMembership, role: WorkspaceRole
) -> WorkspaceMembership:
    """Change a member's role, refusing to leave the workspace ownerless."""
    if (
        membership.role is WorkspaceRole.OWNER
        and role is not WorkspaceRole.OWNER
        and _owner_count(session, membership.workspace_id) <= 1
    ):
        raise ConflictError(
            "This is the workspace's only owner. Promote another member to OWNER "
            "first — a workspace with no owner cannot be administered."
        )
    membership.role = role
    session.flush()
    return membership


def remove_member(session: Session, *, membership: WorkspaceMembership) -> None:
    """Remove a member, refusing to leave the workspace ownerless."""
    if (
        membership.role is WorkspaceRole.OWNER
        and _owner_count(session, membership.workspace_id) <= 1
    ):
        raise ConflictError(
            "This is the workspace's only owner and cannot be removed. Transfer " "ownership first."
        )
    session.delete(membership)
    session.flush()


def transfer_ownership(
    session: Session,
    *,
    workspace: Workspace,
    current: WorkspaceMembership,
    target: WorkspaceMembership,
) -> None:
    """Hand the workspace to another member.

    The outgoing owner becomes an ADMIN rather than being removed: dropping them
    entirely would, in the common case of a single-owner workspace, lock the person
    doing the transfer out of the thing they just handed over.
    """
    if current.workspace_id != workspace.id or target.workspace_id != workspace.id:
        raise ValidationError("Both memberships must belong to this workspace")
    if current.id == target.id:
        raise ValidationError("That member already owns this workspace")
    target.role = WorkspaceRole.OWNER
    current.role = WorkspaceRole.ADMIN
    session.flush()


def workspace_case_count(session: Session, workspace_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Case).where(Case.workspace_id == workspace_id)
        )
        or 0
    )


# ------------------------------------------------------------------ sessions


def create_session(
    session: Session,
    *,
    user: User,
    user_agent: str = "",
    settings: Settings | None = None,
) -> tuple[UserSession, str]:
    """Open a session. Returns the row and the token, which is returned **once**.

    The token is never stored and cannot be recovered from the row: if the caller
    loses it, the only remedy is to sign in again. That is the property that makes
    a stolen database useless for impersonation.
    """
    settings = settings or get_settings()
    token = new_session_token()
    now = datetime.now(UTC)
    # Stamped immediately when the account owes no second factor, so "is this
    # session authenticated" stays one column read. An account with a confirmed
    # factor gets a null, which every route treats as not-yet-signed-in.
    row = UserSession(
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=now + timedelta(hours=settings.session_lifetime_hours),
        last_seen_at=now,
        user_agent=(user_agent or "")[:200],
        mfa_satisfied_at=None if mfa_required(session, user.id) else now,
    )
    user.last_login_at = now
    session.add(row)
    session.flush()
    return row, token


def resolve_session(
    session: Session, token: str, *, settings: Settings | None = None
) -> UserSession | None:
    """The live session for ``token``, or ``None``.

    Enforces three separate conditions, because they fail for different reasons: a
    revoked session (signed out), an expired session (absolute lifetime reached),
    and an idle session (untouched longer than the idle timeout). Any of them means
    "sign in again"; none of them is treated as a valid session with a warning.
    """
    settings = settings or get_settings()
    if not token:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token)))
    if row is None or not row.is_live:
        return None
    now = datetime.now(UTC)
    last_seen = row.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if now - last_seen > timedelta(minutes=settings.session_idle_timeout_minutes):
        row.revoked_at = now
        # Committed here rather than flushed. The caller is about to raise
        # AuthenticationRequired, and the request-scoped session rolls back on an
        # exception — so a flush would be discarded and the idle session would
        # still look live in the table. Safe to commit: this runs in the
        # authentication dependency, before any request has done work of its own.
        session.commit()
        log.info("session.idle_expired", session_id=str(row.id))
        return None
    row.last_seen_at = now
    session.flush()
    return row


def revoke_session(session: Session, row: UserSession) -> None:
    """End one session. Idempotent."""
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.flush()


def revoke_all_sessions(session: Session, user_id: uuid.UUID) -> int:
    """End every session a user holds. Returns how many were live."""
    now = datetime.now(UTC)
    rows = list(
        session.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
    )
    for row in rows:
        row.revoked_at = now
    if rows:
        session.flush()
    return len(rows)


def purge_expired_sessions(session: Session, *, before: datetime | None = None) -> int:
    """Delete sessions that expired before ``before``. For a maintenance task."""
    cutoff = before or datetime.now(UTC)
    rows = list(session.scalars(select(UserSession).where(UserSession.expires_at < cutoff)))
    for row in rows:
        session.delete(row)
    if rows:
        session.flush()
    return len(rows)


def require_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"No user {user_id}")
    return user


# ------------------------------------------------------------------- factors


def mfa_for(session: Session, user_id: uuid.UUID) -> UserMfa | None:
    """The account's MFA row, enrolled or merely started."""
    return session.scalar(select(UserMfa).where(UserMfa.user_id == user_id))


def mfa_required(session: Session, user_id: uuid.UUID) -> bool:
    """Whether this account must clear a second factor to sign in.

    Only a *confirmed* factor counts. A half-finished enrolment — a secret
    written, a QR code never scanned — must never start gating sign-in, or a
    user who abandoned enrolment would be locked out by a factor they never
    proved they had.
    """
    row = mfa_for(session, user_id)
    return row is not None and row.is_active


def satisfy_mfa(session: Session, row: UserSession) -> UserSession:
    """Promote a password-stage session to fully authenticated."""
    row.mfa_satisfied_at = datetime.now(UTC)
    session.flush()
    return row


def begin_mfa_enrollment(session: Session, user: User) -> tuple[UserMfa, str]:
    """Start enrolment, returning the row and the secret to show once.

    Replaces any unconfirmed attempt: somebody who scanned a code into the wrong
    app and started again should get a clean secret, not a second row racing the
    first. A *confirmed* factor is never silently replaced — the caller checks.
    """
    existing = mfa_for(session, user.id)
    if existing is not None:
        session.delete(existing)
        session.flush()
    secret = new_totp_secret()
    row = UserMfa(user_id=user.id, secret=secret)
    session.add(row)
    session.flush()
    return row, secret


def confirm_mfa_enrollment(session: Session, row: UserMfa, step: int) -> list[str]:
    """Activate the factor and issue fresh recovery codes.

    Returns the codes in plaintext **once**; only their Argon2 verifiers are
    stored. Any codes from a previous enrolment are discarded, so a code printed
    before a re-enrolment cannot open the account afterwards.
    """
    now = datetime.now(UTC)
    row.confirmed_at = now
    row.last_used_step = step
    row.last_verified_at = now
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == row.user_id))
    codes = new_recovery_codes()
    for code in codes:
        session.add(MfaRecoveryCode(user_id=row.user_id, code_hash=hash_recovery_code(code)))
    session.flush()
    return codes


def disable_mfa(session: Session, user_id: uuid.UUID) -> bool:
    """Remove the factor and every recovery code. Returns whether one existed."""
    row = mfa_for(session, user_id)
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    if row is None:
        session.flush()
        return False
    session.delete(row)
    session.flush()
    return True


def unused_recovery_codes(session: Session, user_id: uuid.UUID) -> list[MfaRecoveryCode]:
    """Recovery codes that have not been spent."""
    return list(
        session.scalars(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
            )
        )
    )


def consume_recovery_code(session: Session, user_id: uuid.UUID, code: str) -> bool:
    """Spend a recovery code. Returns whether one matched.

    Every unused code is checked, and the first match is marked spent in the same
    transaction as the sign-in it authorises — so the same code cannot be used
    twice, including by two requests arriving together.
    """
    for row in unused_recovery_codes(session, user_id):
        if verify_recovery_code(code, row.code_hash):
            row.used_at = datetime.now(UTC)
            session.flush()
            return True
    return False
