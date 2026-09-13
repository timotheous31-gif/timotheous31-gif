"""The bootstrap CLI: the only way an empty deployment gets its first account.

Worth its own file because this code runs exactly once per installation, by an
operator, at the moment they are least able to debug it — and because it is the
one place in the product that can create an account without an existing one. The
rules it has to keep are: never a default password, never a password on a command
line, never a silent adoption of somebody else's data.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from app.cli.admin import admin_app
from app.core.db import configure_engine, get_session_factory
from app.models import Base
from app.models.auth import User, Workspace
from app.models.case import Case
from app.models.enums import WorkspaceRole
from tests.conftest import TEST_PASSWORD


@pytest.fixture
def database():
    """A fresh in-memory database for one CLI invocation."""
    engine = configure_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def run():
    runner = CliRunner()
    return lambda *args, **kwargs: runner.invoke(admin_app, list(args), **kwargs)


def _unclaimed_case(name: str = "Legacy case") -> str:
    """A case with no workspace — what an installation that predates this PR has."""
    with get_session_factory()() as session:
        case = Case(name=name)
        session.add(case)
        session.commit()
        return str(case.id)


class TestCreateAdmin:
    def test_it_creates_an_owner_and_a_workspace(self, database, run):
        result = run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--display-name",
            "Chief",
            "--workspace",
            "Investigations",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )
        assert result.exit_code == 0, result.output

        with get_session_factory()() as session:
            user = session.query(User).one()
            space = session.query(Workspace).one()
            assert user.email == "chief@example.com"
            assert user.display_name == "Chief"
            assert space.name == "Investigations"
            assert user.memberships[0].role is WorkspaceRole.OWNER

    def test_the_password_is_never_an_option(self):
        """A password on a command line is a password in shell history."""
        import click

        command = typer_command(admin_app, "create-admin")
        names = {
            name
            for parameter in command.params
            if isinstance(parameter, click.Option)
            for name in parameter.opts
        }
        assert not any("password" in name for name in names), names

    def test_the_password_is_not_echoed(self, database, run):
        result = run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )
        assert result.exit_code == 0
        assert TEST_PASSWORD not in result.output

    def test_the_password_is_stored_as_an_argon2_hash(self, database, run):
        run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )
        with get_session_factory()() as session:
            user = session.query(User).one()
        assert user.password_hash.startswith("$argon2id$")
        assert TEST_PASSWORD not in user.password_hash

    def test_a_weak_password_is_refused(self, database, run):
        result = run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
            input="short\nshort\n",
        )
        assert result.exit_code != 0
        with get_session_factory()() as session:
            assert session.query(User).count() == 0

    def test_a_duplicate_address_is_refused(self, database, run):
        args = (
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
        )
        assert run(*args, input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n").exit_code == 0
        second = run(*args, input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n")
        assert second.exit_code != 0
        with get_session_factory()() as session:
            assert session.query(User).count() == 1

    def test_it_warns_when_the_deployment_already_has_accounts(self, database, run):
        args = (
            "create-admin",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
        )
        run(*args, "--email", "first@example.com", input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n")
        second = run(
            *args, "--email", "second@example.com", input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n"
        )
        assert "already has accounts" in second.output

    def test_it_points_at_the_unclaimed_cases_it_found(self, database, run):
        _unclaimed_case()
        result = run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )
        assert "belong to no workspace" in result.output
        assert "claim-cases" in result.output


class TestClaimCases:
    @pytest.fixture
    def workspace_slug(self, database, run) -> str:
        run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm One",
            "--display-name",
            "Chief",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )
        with get_session_factory()() as session:
            return session.query(Workspace).one().slug

    def test_it_adopts_only_the_unclaimed_cases(self, workspace_slug, run):
        """Another workspace's case must not be swept up by an adoption."""
        from app.services import accounts

        orphan = _unclaimed_case()
        with get_session_factory()() as session:
            adopter = session.query(Workspace).one()
            stranger_user = accounts.create_user(
                session, email="stranger@example.com", password=TEST_PASSWORD
            )
            stranger = accounts.create_workspace(session, name="Firm Two", owner=stranger_user)
            theirs = Case(name="Not yours", workspace_id=stranger.id)
            session.add(theirs)
            session.commit()
            adopter_id, stranger_id, theirs_id = adopter.id, stranger.id, theirs.id

        result = run("claim-cases", "--workspace", workspace_slug, "--yes")
        assert result.exit_code == 0, result.output

        with get_session_factory()() as session:
            assert session.get(Case, orphan).workspace_id == adopter_id
            assert session.get(Case, theirs_id).workspace_id == stranger_id

    def test_it_shows_what_it_is_about_to_do_and_stops_if_refused(self, workspace_slug, run):
        orphan = _unclaimed_case("Sensitive legacy case")
        result = run("claim-cases", "--workspace", workspace_slug, input="n\n")
        assert result.exit_code == 1
        assert "Sensitive legacy case" in result.output
        with get_session_factory()() as session:
            assert session.get(Case, orphan).workspace_id is None

    def test_it_refuses_an_unknown_workspace(self, workspace_slug, run):
        orphan = _unclaimed_case()
        result = run("claim-cases", "--workspace", "no-such-workspace", "--yes")
        assert result.exit_code != 0
        with get_session_factory()() as session:
            assert session.get(Case, orphan).workspace_id is None

    def test_it_changes_nothing_but_the_owning_column(self, workspace_slug, run):
        """Adoption must never touch investigation data."""
        orphan = _unclaimed_case("Keep my name")
        with get_session_factory()() as session:
            before = session.get(Case, orphan)
            snapshot = (before.name, before.status, before.created_at)

        run("claim-cases", "--workspace", workspace_slug, "--yes")

        with get_session_factory()() as session:
            after = session.get(Case, orphan)
            assert (after.name, after.status, after.created_at) == snapshot

    def test_machine_readable_output_cannot_prompt(self, workspace_slug, run):
        _unclaimed_case()
        result = run("claim-cases", "--workspace", workspace_slug, "--json")
        assert result.exit_code != 0
        assert "--yes" in result.output


class TestListUnclaimed:
    def test_it_lists_every_unclaimed_case(self, database, run):
        _unclaimed_case("First legacy case")
        _unclaimed_case("Second legacy case")
        result = run("list-unclaimed")
        assert result.exit_code == 0, result.output
        # Regression: the table was once called with its arguments in the wrong
        # order and rendered no rows at all, which would have told an operator
        # they had nothing to adopt.
        assert "First legacy case" in result.output
        assert "Second legacy case" in result.output

    def test_it_changes_nothing(self, database, run):
        orphan = _unclaimed_case()
        run("list-unclaimed")
        with get_session_factory()() as session:
            assert session.get(Case, orphan).workspace_id is None

    def test_it_says_so_when_there_is_nothing_to_adopt(self, database, run):
        result = run("list-unclaimed")
        assert result.exit_code == 0
        assert "belongs to a workspace" in result.output

    def test_the_json_form_carries_the_same_cases(self, database, run):
        import json

        _unclaimed_case("Legacy")
        result = run("list-unclaimed", "--json")
        payload = json.loads(result.output)
        assert payload["unclaimed"] == 1
        assert payload["cases"][0]["name"] == "Legacy"


class TestResetPassword:
    @pytest.fixture
    def account(self, database, run):
        run(
            "create-admin",
            "--email",
            "chief@example.com",
            "--workspace",
            "Firm",
            "--display-name",
            "Chief",
            input=f"{TEST_PASSWORD}\n{TEST_PASSWORD}\n",
        )

    def test_it_changes_the_password(self, account, run):
        from app.core.security import verify_password

        replacement = "a different long passphrase"
        result = run(
            "reset-password",
            "--email",
            "chief@example.com",
            input=f"{replacement}\n{replacement}\n",
        )
        assert result.exit_code == 0, result.output
        with get_session_factory()() as session:
            user = session.query(User).one()
            assert verify_password(replacement, user.password_hash)
            assert not verify_password(TEST_PASSWORD, user.password_hash)

    def test_it_ends_every_session_that_account_holds(self, account, run):
        from app.models.auth import UserSession
        from app.services import accounts

        with get_session_factory()() as session:
            user = session.query(User).one()
            accounts.create_session(session, user=user)
            session.commit()

        replacement = "a different long passphrase"
        run(
            "reset-password",
            "--email",
            "chief@example.com",
            input=f"{replacement}\n{replacement}\n",
        )

        with get_session_factory()() as session:
            rows = session.query(UserSession).all()
            assert rows
            assert all(not row.is_live for row in rows)

    def test_it_refuses_an_unknown_account(self, account, run):
        result = run(
            "reset-password", "--email", "nobody@example.com", input="whatever passphrase\n"
        )
        assert result.exit_code != 0

    def test_it_refuses_a_weak_replacement(self, account, run):
        from app.core.security import verify_password

        result = run("reset-password", "--email", "chief@example.com", input="short\nshort\n")
        assert result.exit_code != 0
        with get_session_factory()() as session:
            user = session.query(User).one()
            assert verify_password(TEST_PASSWORD, user.password_hash)


def typer_command(app, name: str):
    """The click command behind one Typer subcommand."""
    import typer.main

    group = typer.main.get_command(app)
    return group.commands[name]  # type: ignore[attr-defined]
