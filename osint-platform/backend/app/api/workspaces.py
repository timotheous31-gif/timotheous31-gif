"""Workspaces, membership and the audit log.

Membership management is where a pilot deployment's data actually leaks: adding a
member is how an investigation reaches somebody it was not scoped to. So these
routes are the most restrictive in the platform.

* ``MEMBERSHIP_MANAGE`` — ADMIN and OWNER only. An ANALYST runs investigations;
  who else can see them is not their decision.
* ``OWNERSHIP_TRANSFER`` — OWNER only, checked explicitly rather than through the
  role table, because an ADMIN who could transfer ownership could promote
  themselves and demote the owner, which makes the two roles one role.
* ``AUDIT_READ`` — ADMIN and OWNER. The audit log names who did what, and is
  itself workspace-scoped customer data.

A member can never be added at a role higher than the caller's own, so an ADMIN
cannot mint an OWNER and inherit their powers by proxy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    WorkspaceContext,
    parse_uuid,
    require_workspace,
)
from app.core.errors import NotFoundError, PermissionDenied, ValidationError
from app.core.permissions import Permission, permissions_for
from app.core.security import PasswordPolicyError
from app.models.enums import AuditEvent, WorkspaceRole
from app.schemas.auth import (
    AuditEntryRead,
    MembershipRead,
    MembershipWrite,
    RoleChange,
    UserCreate,
    UserRead,
    WorkspaceRead,
    WorkspaceSummary,
)
from app.services import accounts, audit

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

#: Roles ordered by power, so "not above your own" is one comparison rather than
#: a chain of special cases.
_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.ANALYST: 1,
    WorkspaceRole.ADMIN: 2,
    WorkspaceRole.OWNER: 3,
}


def _refuse_escalation(ctx: WorkspaceContext, role: WorkspaceRole) -> None:
    """Refuse to grant a role above the caller's own.

    Without this an ADMIN could create an OWNER and then act through them, which
    is ownership transfer by another name.
    """
    if _RANK[role] > _RANK[ctx.role]:
        raise PermissionDenied(
            f"You cannot grant a role above your own. You are {ctx.role}; this "
            f"request asks for {role}."
        )


def _membership_read(membership) -> MembershipRead:
    return MembershipRead(
        id=membership.id,
        user=UserRead.model_validate(membership.user),
        workspace_id=membership.workspace_id,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.get("", response_model=list[WorkspaceSummary], summary="Your workspaces")
def list_workspaces(principal: CurrentUser, session: DbSession) -> list[WorkspaceSummary]:
    """Every workspace the caller belongs to, and no others."""
    return [
        WorkspaceSummary(
            workspace=WorkspaceRead.model_validate(membership.workspace),
            role=membership.role,
            permissions=permissions_for(membership.role),
        )
        for membership in accounts.memberships_for_user(session, principal.user_id)
    ]


@router.get("/{workspace_id}", response_model=WorkspaceSummary, summary="One workspace")
def get_workspace(ctx: CurrentWorkspace) -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace=WorkspaceRead.model_validate(ctx.workspace),
        role=ctx.role,
        permissions=permissions_for(ctx.role),
    )


@router.get(
    "/{workspace_id}/members",
    response_model=list[MembershipRead],
    summary="Members of a workspace",
)
def list_members(ctx: CurrentWorkspace, session: DbSession) -> list[MembershipRead]:
    """Readable by any member: knowing who else can see your cases is not a
    privilege, it is the point of a shared workspace."""
    return [_membership_read(item) for item in ctx.workspace.memberships]


@router.post(
    "/{workspace_id}/members",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an existing account to the workspace",
)
def add_member(
    payload: MembershipWrite,
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.MEMBERSHIP_MANAGE))],
    session: DbSession,
) -> MembershipRead:
    """Add an account that already exists.

    Deliberately separate from creating an account. A route that silently created
    a user for an unknown address would let one typo hand a stranger's address a
    seat in a customer's workspace.
    """
    _refuse_escalation(ctx, payload.role)
    user = accounts.get_user_by_email(session, payload.email)
    if user is None:
        raise NotFoundError(
            "No account exists for that address. Create the account first, or use "
            "the create-user endpoint, so an invitation cannot go to a typo."
        )
    membership = accounts.add_member(session, workspace=ctx.workspace, user=user, role=payload.role)
    audit.record(
        session,
        event=AuditEvent.MEMBERSHIP_ADDED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=user.id,
        metadata={"role": str(payload.role)},
    )
    session.commit()
    return _membership_read(membership)


@router.post(
    "/{workspace_id}/users",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and add it to the workspace",
)
def create_user(
    payload: UserCreate,
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.MEMBERSHIP_MANAGE))],
    session: DbSession,
) -> MembershipRead:
    """Create an account with a password the administrator sets.

    A pilot deployment has no outbound mail, so there is no invitation link to
    send; the administrator sets an initial password and passes it on out of band.
    The new user can change it at ``POST /auth/password``, which revokes every
    session including the one created from the initial password.
    """
    _refuse_escalation(ctx, payload.role)
    try:
        user = accounts.create_user(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except PasswordPolicyError as exc:
        raise ValidationError(str(exc)) from exc
    membership = accounts.add_member(session, workspace=ctx.workspace, user=user, role=payload.role)
    audit.record(
        session,
        event=AuditEvent.USER_CREATED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=user.id,
        metadata={"role": str(payload.role)},
    )
    audit.record(
        session,
        event=AuditEvent.MEMBERSHIP_ADDED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=user.id,
        metadata={"role": str(payload.role)},
    )
    session.commit()
    return _membership_read(membership)


@router.patch(
    "/{workspace_id}/members/{membership_id}",
    response_model=MembershipRead,
    summary="Change a member's role",
)
def change_member_role(
    membership_id: str,
    payload: RoleChange,
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.MEMBERSHIP_MANAGE))],
    session: DbSession,
) -> MembershipRead:
    """Change a role, within the limits of the caller's own.

    Promoting somebody to OWNER is ownership transfer and goes through the
    dedicated route, which OWNER alone may use.
    """
    _refuse_escalation(ctx, payload.role)
    if payload.role is WorkspaceRole.OWNER:
        raise PermissionDenied(
            "Promoting a member to OWNER is an ownership transfer. Use "
            "POST /workspaces/{workspace_id}/transfer-ownership, which only the "
            "current owner may call."
        )
    membership = _member_or_404(ctx, session, membership_id)
    previous = membership.role
    accounts.change_role(session, membership=membership, role=payload.role)
    audit.record(
        session,
        event=AuditEvent.ROLE_CHANGED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=membership.user_id,
        metadata={"from": str(previous), "to": str(payload.role)},
    )
    session.commit()
    return _membership_read(membership)


@router.delete(
    "/{workspace_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
)
def remove_member(
    membership_id: str,
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.MEMBERSHIP_MANAGE))],
    session: DbSession,
) -> None:
    membership = _member_or_404(ctx, session, membership_id)
    if _RANK[membership.role] > _RANK[ctx.role]:
        raise PermissionDenied("You cannot remove a member whose role outranks yours.")
    user_id = membership.user_id
    accounts.remove_member(session, membership=membership)
    # Removing somebody from a workspace has to end their access to it now, not
    # whenever their session happens to expire.
    accounts.revoke_all_sessions(session, user_id)
    audit.record(
        session,
        event=AuditEvent.MEMBERSHIP_REMOVED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=user_id,
    )
    session.commit()


@router.post(
    "/{workspace_id}/transfer-ownership",
    response_model=list[MembershipRead],
    summary="Hand the workspace to another member",
)
def transfer_ownership(
    payload: RoleChange | None,
    membership_id: Annotated[str, Query(description="Membership to promote")],
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.OWNERSHIP_TRANSFER))],
    session: DbSession,
) -> list[MembershipRead]:
    """Transfer ownership. OWNER only — an ADMIN is refused here.

    The outgoing owner becomes an ADMIN rather than being removed: in the common
    single-owner case, dropping them would lock the person doing the transfer out
    of the thing they just handed over.
    """
    target = _member_or_404(ctx, session, membership_id)
    current = accounts.membership_for(
        session, user_id=ctx.principal.user_id, workspace_id=ctx.workspace_id
    )
    if current is None:  # pragma: no cover - the context guarantees it
        raise NotFoundError("No such workspace")
    accounts.transfer_ownership(session, workspace=ctx.workspace, current=current, target=target)
    audit.record(
        session,
        event=AuditEvent.OWNERSHIP_TRANSFERRED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="user",
        object_id=target.user_id,
    )
    session.commit()
    return [_membership_read(current), _membership_read(target)]


@router.get(
    "/{workspace_id}/audit",
    response_model=list[AuditEntryRead],
    summary="The workspace's security log",
)
def read_audit(
    ctx: Annotated[WorkspaceContext, Depends(require_workspace(Permission.AUDIT_READ))],
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AuditEntryRead]:
    """Read-only, and the only route that touches this table at all.

    There is deliberately no route to edit or delete an audit entry: an audit log
    an administrator can rewrite is not an audit log.
    """
    entries = audit.entries_for_workspace(session, ctx.workspace_id, limit=limit, offset=offset)
    return [AuditEntryRead.model_validate(entry) for entry in entries]


def _member_or_404(ctx: WorkspaceContext, session: DbSession, membership_id: str):
    """Resolve a membership **within this workspace**.

    The workspace check is the point: without it, a membership id from another
    workspace would be edited by whoever guessed it.
    """
    from app.models.auth import WorkspaceMembership

    identifier = parse_uuid(membership_id, "membership_id")
    membership = session.get(WorkspaceMembership, identifier)
    if membership is None or membership.workspace_id != ctx.workspace_id:
        raise NotFoundError("No such member of this workspace")
    return membership
