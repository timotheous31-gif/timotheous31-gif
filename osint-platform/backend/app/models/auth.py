"""Accounts, workspaces, sessions and the audit ledger.

Five tables, and one idea holding them together: **a UUID is not an
authorization**. Before this, any caller who knew a case id could read it,
re-run it, export it or delete it. The fix is not a check bolted onto each
route — it is a containment boundary the routes cannot forget.

That boundary is the **workspace**. A case belongs to exactly one, a user
reaches a workspace only through a membership, and every other table in the
platform — targets, findings, evidence, entities, relationships, timeline
events, collector runs, jobs, observations, social profiles, images, public
contacts, analyst decisions — already carries ``case_id``. So authorizing the
case authorizes all fourteen of them, once, in one place, and a table added
later inherits the boundary by carrying ``case_id`` like its siblings.

Three deliberate choices worth stating.

**Passwords are never stored, only Argon2id verifiers.** The column is named
``password_hash`` and nothing in the codebase ever writes a plaintext password
anywhere — not to the database, not to a log, not to an audit entry.

**Session tokens are never stored either.** What is stored is a SHA-256 of the
token. A stolen database therefore yields no usable session, and a session row
cannot be replayed by whoever can read the table.

**The audit ledger is append-only by construction.** It has no update route, no
delete route, and no ORM relationship that would cascade a delete into it. A
workspace or a user can be removed; the record that they acted stays.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow
from app.models.enums import AuditEvent, WorkspaceRole
from app.models.types import GUID, JSONType

if TYPE_CHECKING:
    from app.models.case import Case


class User(UUIDMixin, TimestampMixin, Base):
    """One person who can sign in.

    ``email`` is stored already lowercased and is the login identifier. It is
    unique across the deployment rather than per workspace, because one human
    with one address belongs to however many workspaces they are invited to —
    duplicating them per workspace would mean duplicating their credential.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    #: An Argon2id verifier. Never a password, never reversible, never logged.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: A deactivated account keeps its history and its audit trail but cannot
    #: authenticate. Preferred over deletion, which would orphan the record of
    #: what the account did.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="noload"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately no hash, no email domain games: just the id, so a stray
        # repr in a log or a traceback discloses nothing about the person.
        return f"<User {self.id}>"


class Workspace(UUIDMixin, TimestampMixin, Base):
    """A tenant. Every case belongs to exactly one."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: A stable, URL-safe handle. Unique so an operator can name a workspace on
    #: the command line without quoting a UUID.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", lazy="selectin"
    )
    cases: Mapped[list[Case]] = relationship(back_populates="workspace", lazy="noload")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Workspace {self.slug}>"


class WorkspaceMembership(UUIDMixin, TimestampMixin, Base):
    """What one user may do in one workspace.

    The unique constraint is the authorization invariant: a user has at most one
    role per workspace, so "which role applies" never depends on row order.
    """

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
        Index("ix_membership_workspace_role", "workspace_id", "role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        SAEnum(WorkspaceRole, name="workspace_role", native_enum=False, length=20),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships", lazy="joined")


class UserSession(UUIDMixin, TimestampMixin, Base):
    """One signed-in browser.

    The token itself is never stored. ``token_hash`` is a SHA-256 of the opaque
    value handed to the client, so reading this table yields nothing that can be
    replayed. Revocation is a timestamp rather than a delete, so "this session
    was used, then logged out" stays legible after the fact.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_sessions_user_expires", "user_id", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: Truncated, for an operator recognising their own sessions. Never an IP
    #: address: this table exists to authenticate, not to log where somebody was.
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    user: Mapped[User] = relationship(back_populates="sessions", lazy="joined")

    @property
    def is_live(self) -> bool:
        """Usable right now: not revoked, not expired."""
        if self.revoked_at is not None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            # SQLite hands back naive datetimes; compare like with like rather
            # than raising inside an authentication path.
            expires = expires.replace(tzinfo=UTC)
        return expires > datetime.now(UTC)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserSession {self.id} user={self.user_id}>"


class AuditLogEntry(UUIDMixin, Base):
    """One security-relevant thing that happened. Append-only.

    No ``updated_at``, because an audit entry is never updated. No cascade from
    users or workspaces, because deleting an actor must not delete the evidence
    that they acted — the foreign keys are deliberately ``SET NULL``.

    ``metadata_`` carries only what a reviewer needs to understand the event: a
    case name, a role, a count. Never a password, a token, an API key, an
    Authorization header, or a raw request body. :func:`app.services.audit.record`
    is the only writer and it enforces that.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_workspace_time", "workspace_id", "occurred_at"),
        Index("ix_audit_actor_time", "actor_user_id", "occurred_at"),
        Index("ix_audit_type_time", "event_type", "occurred_at"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    #: Null for an unauthenticated event — a failed login against an address
    #: that does not exist has no actor, and inventing one would be a lie.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("workspaces.id", ondelete="SET NULL"), default=None, index=True
    )
    event_type: Mapped[AuditEvent] = mapped_column(
        SAEnum(AuditEvent, name="audit_event", native_enum=False, length=40), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: The request that caused it, for joining against application logs.
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONType, default=dict, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLogEntry {self.event_type} {self.object_type}:{self.object_id}>"
