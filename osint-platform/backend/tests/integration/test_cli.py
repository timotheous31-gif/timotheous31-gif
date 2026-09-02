"""CLI behaviour, driven through Typer's runner against a temporary database."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from app.cli.main import app


@pytest.fixture
def runner(tmp_path):
    """A CLI runner bound to a file-backed SQLite database."""
    from app.core.db import configure_engine
    from app.models import Base

    url = f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"
    engine = configure_engine(url)
    Base.metadata.create_all(engine)
    cli = CliRunner()
    cli.default_args = ["--database-url", url]
    yield cli
    Base.metadata.drop_all(engine)


def invoke(runner, *args):
    result = runner.invoke(app, [*runner.default_args, *args])
    return result


def test_version_json(runner):
    result = invoke(runner, "version", "--json")
    assert result.exit_code == 0
    assert "version" in result.stdout


def test_normalize_reports_inferred_type(runner):
    result = invoke(runner, "normalize", "@ExampleUser", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["type"] == "USERNAME"
    assert payload["normalized_value"] == "exampleuser"


def test_case_and_target_lifecycle(runner):
    created = invoke(runner, "case", "create", "Acme Investigation", "--json")
    assert created.exit_code == 0, created.stdout
    case_id = json.loads(created.stdout)["id"]

    added = invoke(runner, "target", "add", "--case", case_id, "--domain", "example.com", "--json")
    assert added.exit_code == 0
    assert json.loads(added.stdout)["normalized_value"] == "example.com"

    listing = invoke(runner, "target", "list", "--case", case_id, "--json")
    assert json.loads(listing.stdout)["total"] == 1

    shown = invoke(runner, "case", "show", case_id, "--json")
    assert json.loads(shown.stdout)["targets"] == 1

    cases = invoke(runner, "case", "list", "--json")
    assert json.loads(cases.stdout)["total"] == 1

    deleted = invoke(runner, "case", "delete", case_id, "--yes")
    assert deleted.exit_code == 0


def test_target_add_infers_type_from_positional_value(runner):
    case_id = json.loads(invoke(runner, "case", "create", "C", "--json").stdout)["id"]
    result = invoke(runner, "target", "add", "--case", case_id, "octocat/Hello-World", "--json")
    assert json.loads(result.stdout)["type"] == "REPOSITORY"


def test_target_add_requires_exactly_one_target(runner):
    case_id = json.loads(invoke(runner, "case", "create", "C", "--json").stdout)["id"]
    none_given = invoke(runner, "target", "add", "--case", case_id)
    assert none_given.exit_code != 0

    two_given = invoke(
        runner, "target", "add", "--case", case_id, "--domain", "a.test", "--username", "b"
    )
    assert two_given.exit_code != 0


def test_bad_uuid_is_rejected(runner):
    result = invoke(runner, "case", "show", "not-a-uuid")
    assert result.exit_code != 0
