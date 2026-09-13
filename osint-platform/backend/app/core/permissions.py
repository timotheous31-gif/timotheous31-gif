"""What each role may do, as one table nobody has to infer.

The alternative — scattering ``if role == "ADMIN"`` through forty routes — is how
an authorization model ends up with a hole in it that nobody can find by reading.
So there is exactly one mapping here, every route names the permission it needs,
and a reviewer answers "what can a VIEWER do?" by reading one dict.

Two rules the matrix encodes deliberately:

**A VIEWER cannot change anything.** Not a case, not a target, not an analyst
decision, not an import. Reading a case, its evidence and its reports is the
whole of the role — which is what makes it safe to hand to a client, an auditor
or a colleague who only needs the answer.

**An ANALYST cannot manage people.** They run investigations, import results,
record decisions and export reports. Who else is in the workspace is not their
call, because adding a member is how an investigation's data leaves the people it
was scoped to.

And one rule the matrix cannot encode, so :func:`allows` enforces it directly:
**only an OWNER transfers ownership.** An ADMIN with that power could promote
themselves and demote the owner, which makes ADMIN and OWNER the same role with
two names.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import WorkspaceRole


class Permission(StrEnum):
    """One thing a member might be allowed to do.

    Named for the action rather than the route, so moving an endpoint does not
    change what it requires.
    """

    # --- reading -----------------------------------------------------------
    #: Read a case and everything reachable through it: targets, findings,
    #: entities, relationships, timeline, evidence metadata, runs, profiles,
    #: images, contacts, decisions, recon plans.
    CASE_READ = "case:read"
    #: Generate and export a report.
    REPORT_EXPORT = "report:export"
    #: Read the workspace's audit log.
    AUDIT_READ = "audit:read"

    # --- investigation work -------------------------------------------------
    CASE_CREATE = "case:create"
    CASE_UPDATE = "case:update"
    CASE_DELETE = "case:delete"
    #: Start an investigation, or run the provider search stage.
    INVESTIGATION_RUN = "investigation:run"
    #: Cancel a running investigation.
    INVESTIGATION_CANCEL = "investigation:cancel"
    #: Add or remove targets.
    TARGET_WRITE = "target:write"
    #: Import manually-found search results.
    RESULT_IMPORT = "result:import"
    #: Record or withdraw an analyst decision.
    ANALYST_DECIDE = "analyst:decide"

    # --- workspace administration ------------------------------------------
    WORKSPACE_UPDATE = "workspace:update"
    #: Invite, remove, and change the role of another member.
    MEMBERSHIP_MANAGE = "membership:manage"
    #: Hand the workspace to somebody else. OWNER only, always.
    OWNERSHIP_TRANSFER = "ownership:transfer"


#: Everything a VIEWER may do. The floor of the model.
_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.CASE_READ,
        Permission.REPORT_EXPORT,
    }
)

#: An ANALYST does investigation work, and nothing about people.
_ANALYST: frozenset[Permission] = _VIEWER | {
    Permission.CASE_CREATE,
    Permission.CASE_UPDATE,
    Permission.INVESTIGATION_RUN,
    Permission.INVESTIGATION_CANCEL,
    Permission.TARGET_WRITE,
    Permission.RESULT_IMPORT,
    Permission.ANALYST_DECIDE,
}

#: An ADMIN additionally deletes cases and manages membership. Not ownership.
_ADMIN: frozenset[Permission] = _ANALYST | {
    Permission.CASE_DELETE,
    Permission.WORKSPACE_UPDATE,
    Permission.MEMBERSHIP_MANAGE,
    Permission.AUDIT_READ,
}

#: An OWNER may also give the workspace away.
_OWNER: frozenset[Permission] = _ADMIN | {Permission.OWNERSHIP_TRANSFER}

#: The whole authorization model, in one place.
ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.VIEWER: _VIEWER,
    WorkspaceRole.ANALYST: _ANALYST,
    WorkspaceRole.ADMIN: _ADMIN,
    WorkspaceRole.OWNER: _OWNER,
}

#: Read by the frontend so a button can be hidden — never so a check can be
#: skipped. The backend is authoritative and re-checks every request.
ROLE_DESCRIPTIONS: dict[WorkspaceRole, str] = {
    WorkspaceRole.OWNER: "Everything, including transferring the workspace.",
    WorkspaceRole.ADMIN: "Everything except transferring the workspace.",
    WorkspaceRole.ANALYST: (
        "Run investigations, import results, decide, export. No member management."
    ),
    WorkspaceRole.VIEWER: "Read cases, evidence and reports. No execution, deletion or decisions.",
}


def allows(role: WorkspaceRole, permission: Permission) -> bool:
    """Whether ``role`` may perform ``permission``.

    ``OWNERSHIP_TRANSFER`` is checked against OWNER explicitly rather than left to
    the table alone. Belt and braces on the one permission whose escalation would
    collapse two roles into one.
    """
    if permission is Permission.OWNERSHIP_TRANSFER:
        return role is WorkspaceRole.OWNER
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permissions_for(role: WorkspaceRole) -> list[str]:
    """A sorted, serialisable list, for ``/auth/me`` and the role-aware UI."""
    return sorted(str(item) for item in ROLE_PERMISSIONS.get(role, frozenset()))
