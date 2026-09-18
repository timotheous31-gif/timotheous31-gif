"""Bootstrap commands: the first administrator, and adopting existing cases.

Two commands, both deliberately outside the HTTP API.

``create-admin`` exists because a deployment with no accounts has no way to
create one over an API that requires an account, and the usual answers to that
are all bad. A seeded ``admin/admin`` is a credential every reader of this
repository knows. A "first request wins" bootstrap endpoint is a race anybody on
the network can enter. A password printed by an installer ends up in shell
history and in a screenshot. So the first account is created by somebody with a
shell on the machine, which is a privilege they already have, and the password is
never defaulted, never generated, and never echoed.

``claim-cases`` exists because of the upgrade path. An installation that predates
workspaces has cases with no owner, and the migration deliberately does not guess
one — see ``docs/pilot-deployment.md``. This command is how an operator adopts
them, on purpose, having been shown exactly what is about to happen.
"""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.output import console, emit_json, emit_table, success, warn
from app.core.errors import ConflictError, ValidationError
from app.core.security import MIN_PASSWORD_LENGTH, PasswordPolicyError

admin_app = typer.Typer(name="admin", help="Bootstrap accounts and adopt existing cases.")


def _session():
    from app.core.db import get_session_factory

    return get_session_factory()()


@admin_app.command("create-admin")
def create_admin(
    email: Annotated[str | None, typer.Option("--email", help="Sign-in address.")] = None,
    workspace: Annotated[
        str | None, typer.Option("--workspace", help="Name of the workspace to create.")
    ] = None,
    display_name: Annotated[
        str | None, typer.Option("--display-name", help="Shown in the interface.")
    ] = None,
) -> None:
    """Create the first account and its workspace.

    Prompts for anything not supplied. The password is *always* prompted — never
    an option, never an argument, never an environment variable — because a
    password on a command line is a password in shell history, in the process
    list, and in whatever collects them both.
    """
    from app.models.enums import WorkspaceRole
    from app.services import accounts

    console.print("[bold]Create the first administrator[/bold]")
    console.print("This account owns a new workspace and can invite the rest of the team.\n")

    address = (email or typer.prompt("Email address")).strip()
    name = (display_name or typer.prompt("Display name", default=address.split("@")[0])).strip()
    space = (workspace or typer.prompt("Workspace name", default="Investigations")).strip()

    password = typer.prompt(
        f"Password (at least {MIN_PASSWORD_LENGTH} characters)",
        hide_input=True,
        confirmation_prompt=True,
    )

    with _session() as session:
        from app.models.auth import User

        if session.query(User).count():
            warn(
                "This deployment already has accounts. Use the workspace member "
                "endpoints to add another user, so the new account is invited into "
                "an existing workspace rather than into one of its own."
            )
        try:
            user = accounts.create_user(
                session, email=address, password=password, display_name=name
            )
            created = accounts.create_workspace(session, name=space, owner=user)
        except PasswordPolicyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        except (ConflictError, ValidationError) as exc:
            raise typer.BadParameter(exc.message) from exc

        from app.models.enums import AuditEvent
        from app.services import audit

        audit.record(
            session,
            event=AuditEvent.USER_CREATED,
            actor_user_id=user.id,
            workspace_id=created.id,
            object_type="user",
            object_id=user.id,
            metadata={"via": "cli", "role": str(WorkspaceRole.OWNER)},
        )
        unclaimed = _unclaimed_count(session)
        session.commit()

        success(f"Created {address} as OWNER of workspace {created.slug!r}.")
        if unclaimed:
            console.print(
                f"\n[yellow]{unclaimed} case(s) in this database belong to no "
                f"workspace.[/yellow] They predate workspaces and are invisible to "
                f"the API until an operator adopts them:\n\n"
                f"    python -m app.cli admin claim-cases --workspace {created.slug}\n"
            )


@admin_app.command("claim-cases")
def claim_cases(
    workspace: Annotated[str, typer.Option("--workspace", help="Workspace slug.")],
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Adopt every case that belongs to no workspace.

    Shows what it is about to do before doing it. The operation is reversible by
    setting ``workspace_id`` back to NULL, and it neither reads nor rewrites any
    investigation data — only the owning column changes.
    """
    from app.models.case import Case
    from app.services import accounts

    with _session() as session:
        space = accounts.get_workspace_by_slug(session, workspace)
        if space is None:
            raise typer.BadParameter(f"No workspace with the handle {workspace!r}")

        orphans = list(
            session.query(Case).filter(Case.workspace_id.is_(None)).order_by(Case.created_at)
        )
        if not orphans:
            if as_json:
                emit_json({"workspace": space.slug, "claimed": 0})
            else:
                success("No unclaimed cases. Nothing to do.")
            return

        if as_json and not yes:
            raise typer.BadParameter("--json requires --yes, since it cannot prompt")

        if not as_json:
            emit_table(
                f"{len(orphans)} unclaimed case(s) -> workspace {space.slug!r}",
                [
                    {
                        "case": str(case.id),
                        "name": case.name,
                        "created": case.created_at.strftime("%Y-%m-%d"),
                    }
                    for case in orphans[:50]
                ],
                ["case", "name", "created"],
            )
            if len(orphans) > 50:
                console.print(f"... and {len(orphans) - 50} more")
            console.print(
                "\nEveryone in this workspace will be able to read these cases, "
                "subject to their role."
            )
        if not yes and not typer.confirm(f"Adopt {len(orphans)} case(s)?"):
            warn("Nothing was changed.")
            # Deliberately no audit entry: nothing was claimed, and a ledger that
            # records intentions rather than changes cannot be read as a record of
            # what happened.
            raise typer.Exit(code=1)

        from app.models.enums import AuditEvent
        from app.services import audit

        operator_user, operator_host = _operator()
        for case in orphans:
            case.workspace_id = space.id
            # One entry per case, which is what the schema already expresses:
            # object_id is the case, workspace_id is who received it, occurred_at
            # is when. No new shape, no list packed into metadata that a reader
            # would have to parse back out.
            audit.record(
                session,
                event=AuditEvent.CASE_CLAIMED,
                workspace_id=space.id,
                object_type="case",
                object_id=case.id,
                metadata={
                    "workspace": space.slug,
                    "case_name": case.name,
                    "claimed_total": len(orphans),
                    "via": "cli",
                    "operator_user": operator_user,
                    "operator_host": operator_host,
                },
            )
        session.commit()

        if as_json:
            emit_json({"workspace": space.slug, "claimed": len(orphans)})
        else:
            success(f"Adopted {len(orphans)} case(s) into {space.slug!r}.")


@admin_app.command("list-unclaimed")
def list_unclaimed(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show cases that belong to no workspace, without changing anything."""
    from app.models.case import Case

    with _session() as session:
        orphans = list(
            session.query(Case).filter(Case.workspace_id.is_(None)).order_by(Case.created_at)
        )
        if as_json:
            emit_json(
                {
                    "unclaimed": len(orphans),
                    "cases": [{"id": str(c.id), "name": c.name} for c in orphans],
                }
            )
            return
        if not orphans:
            success("Every case belongs to a workspace.")
            return
        emit_table(
            f"{len(orphans)} unclaimed case(s)",
            [
                {
                    "case": str(case.id),
                    "name": case.name,
                    "created": case.created_at.strftime("%Y-%m-%d"),
                }
                for case in orphans
            ],
            ["case", "name", "created"],
        )


@admin_app.command("reset-password")
def reset_password(
    email: Annotated[str, typer.Option("--email", help="Whose password to reset.")],
) -> None:
    """Set a user's password from the shell, ending every session they hold.

    For the pilot's one unavoidable support case: somebody is locked out and
    there is no outbound mail to send a reset link with. Revoking their sessions
    is part of the operation, not a separate step.
    """
    from app.services import accounts

    with _session() as session:
        user = accounts.get_user_by_email(session, email)
        if user is None:
            raise typer.BadParameter(f"No account for {email!r}")
        password = typer.prompt(
            f"New password (at least {MIN_PASSWORD_LENGTH} characters)",
            hide_input=True,
            confirmation_prompt=True,
        )
        try:
            accounts.set_password(session, user, password)
        except PasswordPolicyError as exc:
            raise typer.BadParameter(str(exc)) from exc

        from app.models.enums import AuditEvent
        from app.services import audit

        operator_user, operator_host = _operator()
        had_mfa = accounts.mfa_required(session, user.id)
        audit.record(
            session,
            event=AuditEvent.PASSWORD_CHANGED,
            # The account whose password changed — not the operator, who has no
            # application identity in a shell.
            object_type="user",
            object_id=user.id,
            metadata={
                "by": "admin-cli",
                "subject_email": user.email,
                "sessions_revoked": True,
                # Stated explicitly because the alternative — an admin reset that
                # quietly cleared the second factor — would turn shell access into
                # account takeover. It does not, and the record says so.
                "mfa_left_enabled": had_mfa,
                "via": "cli",
                "operator_user": operator_user,
                "operator_host": operator_host,
            },
        )
        session.commit()
    success(f"Password changed for {email}. Every session for that account was ended.")


def _operator() -> tuple[str, str]:
    """Who ran this command, as well as a shell can answer it.

    There is no signed-in user here — that is the whole point of a bootstrap CLI —
    so ``actor_user_id`` stays null rather than being attributed to somebody who
    did not do it. What can be recorded honestly is the account and the host the
    command ran on, which is what an operator would be asked for afterwards.

    Returned as two values, and stored as two fields, deliberately. Joining them
    into ``user@host`` would produce exactly the shape this platform forbids
    itself from constructing — see
    ``test_no_email_address_is_ever_constructed_from_a_name_and_a_domain`` — and
    an audit reader is better served by two fields they can filter on than by one
    they have to split.
    """
    import getpass
    import socket

    try:
        who = getpass.getuser()
    except Exception:  # pragma: no cover - no passwd entry for the uid
        who = "unknown"
    try:
        where = socket.gethostname()
    except Exception:  # pragma: no cover - defensive
        where = "unknown"
    return who[:100], where[:100]


def _unclaimed_count(session) -> int:
    from app.models.case import Case

    return session.query(Case).filter(Case.workspace_id.is_(None)).count()
