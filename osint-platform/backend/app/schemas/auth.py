"""Request and response models for accounts, workspaces and the audit log.

One rule governs every model here: **a response never carries a credential.**
There is no field for a password, a password hash, a session token or a CSRF
token anywhere in this module, so a serialisation mistake cannot leak one — the
shape simply has nowhere to put it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.models.enums import AuditEvent, WorkspaceRole


class LoginRequest(BaseModel):
    """Sign-in credentials.

    ``password`` is bounded at both ends: too short is a policy failure, and
    unbounded input is a denial-of-service against a deliberately slow hash.
    """

    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class UserRead(BaseModel):
    """A user, as anybody in their workspace may see them."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class MembershipRead(BaseModel):
    """One member of one workspace, with the role and what it permits."""

    id: uuid.UUID
    user: UserRead
    workspace_id: uuid.UUID
    role: WorkspaceRole
    created_at: datetime


class WorkspaceSummary(BaseModel):
    """A workspace as it appears to one caller: the workspace plus their role.

    ``permissions`` is here so the frontend can hide what the caller cannot do.
    It is a convenience for the interface and **not** an authorization decision:
    the backend re-checks every request, and a client that ignored this list
    entirely would gain nothing.
    """

    workspace: WorkspaceRead
    role: WorkspaceRole
    permissions: list[str] = Field(default_factory=list)


class SessionInfo(BaseModel):
    """What the caller needs to keep a session working.

    ``csrf_token`` appears here — and only here — because the client has to echo
    it in a header on every state-changing request. It is not a credential on its
    own: it authorises nothing without the session cookie, which JavaScript
    cannot read.
    """

    user: UserRead
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)
    csrf_token: str
    expires_at: datetime
    #: True between the password stage and the authenticator code. While it is
    #: true this session can reach nothing but the challenge and sign-out, so a
    #: client that ignores it simply gets 401s rather than access.
    mfa_required: bool = False


class MembershipWrite(BaseModel):
    """Invite an existing account into a workspace at a role."""

    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.VIEWER


class RoleChange(BaseModel):
    role: WorkspaceRole


class UserCreate(BaseModel):
    """Create an account and add it to the caller's workspace."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: str = Field(default="", max_length=200)
    role: WorkspaceRole = WorkspaceRole.VIEWER


class AuditEntryRead(BaseModel):
    """One audit entry. Carries no credential by construction."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    event_type: AuditEvent
    object_type: str
    object_id: str
    request_id: str
    metadata: dict = Field(default_factory=dict, alias="metadata_")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
