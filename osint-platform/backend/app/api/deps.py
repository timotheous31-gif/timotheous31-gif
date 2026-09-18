"""Shared FastAPI dependencies, including the authorization boundary.

Before this module had an owner, every route took ``case_id`` and looked the case
up by id. Anyone holding a UUID held the case. The replacement is one idea applied
uniformly:

    A route never resolves a case. It asks for a :data:`CaseContext`, which
    resolves the case *and* the caller's membership of the workspace that owns
    it, in one query, and hands back both or refuses.

That is why the authorization cannot be forgotten at a call site: there is no
dependency that yields a case without also yielding the right to have it.

Two conventions that are easy to get wrong and are settled here:

**Not-found and not-yours look identical.** A case in another workspace answers
404, not 403. A 403 would confirm that the id names a real case, which is exactly
what an attacker enumerating UUIDs is trying to learn. 403 is reserved for the
case the caller *can* see but is not allowed to act on — a VIEWER attempting a
delete — where the distinction is useful to them and discloses nothing.

**Permissions are named, not inferred.** A route declares
``Depends(require(Permission.CASE_DELETE))`` rather than checking a role inline,
so the whole authorization surface can be read off the route signatures and
compared against :mod:`app.core.permissions`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AuthenticationRequired, CsrfError, PermissionDenied
from app.core.logging import get_logger
from app.core.permissions import Permission, allows
from app.core.security import csrf_token, tokens_match
from app.core.settings import Settings, get_settings
from app.models.auth import User, UserSession, Workspace, WorkspaceMembership
from app.models.case import Case
from app.models.enums import WorkspaceRole
from app.services import accounts

log = get_logger(__name__)

DbSession = Annotated[Session, Depends(get_db)]

#: Methods that change state and therefore need a CSRF token. ``GET``, ``HEAD``
#: and ``OPTIONS`` are exempt because they must be safe — and a ``GET`` that
#: changes state would be a bug regardless of CSRF.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    """Parse ``value`` as a UUID or raise a 422."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be a UUID",
        ) from exc


def case_id_param(case_id: Annotated[str, Path(description="Case UUID")]) -> uuid.UUID:
    return parse_uuid(case_id, "case_id")


CaseId = Annotated[uuid.UUID, Depends(case_id_param)]


def settings_dep() -> Settings:
    return get_settings()


AppSettings = Annotated[Settings, Depends(settings_dep)]


# ------------------------------------------------------------ authentication


@dataclass(slots=True)
class Principal:
    """Who is making this request, and with what session."""

    user: User
    session: UserSession

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


def _session_token(request: Request, settings: Settings) -> str:
    """The session token from the cookie.

    Cookie only. There is deliberately no ``Authorization: Bearer`` fallback: a
    token readable by JavaScript is a token an XSS can exfiltrate, and offering
    both schemes would mean the weaker one decides the security of the system.
    """
    return request.cookies.get(settings.session_cookie_name, "")


def current_principal(request: Request, session: DbSession, settings: AppSettings) -> Principal:
    """Resolve the caller, or raise 401."""
    token = _session_token(request, settings)
    row = accounts.resolve_session(session, token, settings=settings)
    if row is None:
        raise AuthenticationRequired("Sign in to continue")
    user = row.user
    if user is None or not user.is_active:
        # Deactivated between sign-in and now: end the session rather than
        # letting an existing cookie outlive the account it belongs to.
        if row is not None:
            accounts.revoke_session(session, row)
        raise AuthenticationRequired("Sign in to continue")
    request.state.user_id = str(user.id)
    return Principal(user=user, session=row)


CurrentUser = Annotated[Principal, Depends(current_principal)]


def enforce_csrf(request: Request, principal: CurrentUser, settings: AppSettings) -> None:
    """Double-submit CSRF check on every state-changing request.

    ``SameSite=Lax`` already refuses the classic cross-site form post, and it is
    *not* treated as sufficient here. It is one browser's policy rather than this
    application's, it does not cover a sibling host under the same registrable
    domain, and a customer's investigation data is not the place to find out where
    the gaps are. So the client must also echo a token it can only have obtained
    from a response to this session.

    The token is derived from the session id under the deployment secret, so the
    server stores nothing extra and an attacker who cannot read the session cookie
    cannot compute it.
    """
    if request.method not in UNSAFE_METHODS:
        return
    expected = csrf_token(str(principal.session.id), settings.session_secret_value())
    supplied = request.headers.get("x-csrf-token") or request.headers.get("X-CSRF-Token")
    if not tokens_match(expected, supplied):
        log.warning("csrf.rejected", method=request.method, path=request.url.path)
        raise CsrfError(
            "This request is missing a valid CSRF token. Reload the page and try "
            "again; if it keeps happening, sign out and back in."
        )


#: Applied to every state-changing route through the router, not per endpoint, so
#: a new route cannot be added without it.
CsrfGuard = Annotated[None, Depends(enforce_csrf)]


# ------------------------------------------------------------- authorization


@dataclass(slots=True)
class WorkspaceContext:
    """A caller, a workspace, and the role connecting them."""

    principal: Principal
    workspace: Workspace
    membership: WorkspaceMembership

    @property
    def role(self) -> WorkspaceRole:
        return self.membership.role

    @property
    def user(self) -> User:
        return self.principal.user

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    def allows(self, permission: Permission) -> bool:
        return allows(self.role, permission)

    def require(self, permission: Permission) -> None:
        """Raise 403 unless the caller's role permits ``permission``."""
        if not self.allows(permission):
            raise PermissionDenied(
                f"Your role in this workspace ({self.role}) does not permit this "
                f"action ({permission}). Ask an administrator of the workspace if "
                f"you need it."
            )


@dataclass(slots=True)
class CaseContext(WorkspaceContext):
    """A workspace context that has already resolved, and authorized, a case."""

    case: Case

    @property
    def case_id(self) -> uuid.UUID:
        return self.case.id


def _membership_or_404(
    session: Session, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> WorkspaceMembership:
    membership = accounts.membership_for(session, user_id=user_id, workspace_id=workspace_id)
    if membership is None:
        # 404, not 403: a caller who is not a member must not learn that this
        # workspace exists.
        from app.core.errors import NotFoundError

        raise NotFoundError("No such workspace")
    return membership


def workspace_context(
    workspace_id: Annotated[str, Path(description="Workspace UUID")],
    principal: CurrentUser,
    session: DbSession,
) -> WorkspaceContext:
    """Resolve a workspace the caller belongs to, or 404."""
    from app.core.errors import NotFoundError

    identifier = parse_uuid(workspace_id, "workspace_id")
    workspace = session.get(Workspace, identifier)
    if workspace is None:
        raise NotFoundError("No such workspace")
    membership = _membership_or_404(session, user_id=principal.user_id, workspace_id=identifier)
    return WorkspaceContext(principal=principal, workspace=workspace, membership=membership)


CurrentWorkspace = Annotated[WorkspaceContext, Depends(workspace_context)]


def case_context(
    case_id: CaseId,
    principal: CurrentUser,
    session: DbSession,
) -> CaseContext:
    """Resolve a case the caller may see, or 404.

    One query joins case to membership, so "does this case exist" and "may this
    caller have it" are answered together and cannot drift apart.

    An **unclaimed** case — one that predates workspaces and has not been adopted
    — has a null ``workspace_id`` and matches no membership, so it is invisible
    here. That is the intended behaviour and is why ``claim-cases`` exists.
    """
    from app.core.errors import NotFoundError

    row = session.execute(
        select(Case, WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == Case.workspace_id)
            & (WorkspaceMembership.user_id == principal.user_id),
        )
        .where(Case.id == case_id)
    ).first()
    if row is None:
        # Covers every reason at once, on purpose: no such case, a case in another
        # workspace, and an unclaimed case all answer identically.
        raise NotFoundError(f"Case {case_id} does not exist")
    case, membership, workspace = row
    return CaseContext(principal=principal, workspace=workspace, membership=membership, case=case)


CurrentCase = Annotated[CaseContext, Depends(case_context)]


def require(permission: Permission) -> Callable[[CaseContext], CaseContext]:
    """A dependency that admits only callers holding ``permission`` on the case.

    Used as ``ctx: Annotated[CaseContext, Depends(require(Permission.CASE_DELETE))]``,
    which makes the requirement part of the route's signature rather than a line
    somewhere in its body that a refactor can drop.
    """

    def dependency(context: CurrentCase) -> CaseContext:
        context.require(permission)
        return context

    return dependency


def require_workspace(permission: Permission) -> Callable[[WorkspaceContext], WorkspaceContext]:
    """The same, for routes scoped to a workspace rather than a case."""

    def dependency(context: CurrentWorkspace) -> WorkspaceContext:
        context.require(permission)
        return context

    return dependency


def accessible_workspace_ids(session: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every workspace the caller belongs to.

    The filter for list endpoints: a listing must show the caller's own cases and
    nothing else, and doing that by filtering the query is safer than fetching
    everything and discarding — a discard that is forgotten leaks, a filter that is
    forgotten returns nothing.
    """
    return [
        membership.workspace_id for membership in accounts.memberships_for_user(session, user_id)
    ]


@dataclass(slots=True)
class JobContext(CaseContext):
    """A case context reached through a job id rather than a case id.

    ``/jobs/{job_id}`` and ``/jobs/{job_id}/cancel`` do not carry a case in the
    path, which made them the most exposed routes in the platform: a job UUID was
    enough to read an investigation's progress or stop it, in anybody's workspace.
    Resolving job -> case -> membership here closes that without changing the URL.
    """

    job: object


def job_context(
    job_id: Annotated[str, Path(description="Job UUID")],
    principal: CurrentUser,
    session: DbSession,
) -> JobContext:
    """Resolve a job the caller may see, or 404."""
    from app.core.errors import NotFoundError
    from app.models.job import Job

    identifier = parse_uuid(job_id, "job_id")
    row = session.execute(
        select(Job, Case, WorkspaceMembership, Workspace)
        .join(Case, Case.id == Job.case_id)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == Case.workspace_id)
            & (WorkspaceMembership.user_id == principal.user_id),
        )
        .where(Job.id == identifier)
    ).first()
    if row is None:
        raise NotFoundError(f"Job {identifier} does not exist")
    job, case, membership, workspace = row
    return JobContext(
        principal=principal,
        workspace=workspace,
        membership=membership,
        case=case,
        job=job,
    )


CurrentJob = Annotated[JobContext, Depends(job_context)]


def require_job(permission: Permission) -> Callable[[JobContext], JobContext]:
    """Admit only callers holding ``permission`` on the job's case."""

    def dependency(context: CurrentJob) -> JobContext:
        context.require(permission)
        return context

    return dependency
