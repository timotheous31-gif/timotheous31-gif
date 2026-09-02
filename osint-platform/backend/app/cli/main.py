"""``osint`` — the command-line interface.

Every command works against the same service layer as the HTTP API, so results
are identical whichever entry point you use.

Examples::

    osint case create "Acme Investigation"
    osint target add --case CASE_ID --domain example.com
    osint investigate --domain example.com
    osint report --case CASE_ID --format html
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

import typer

from app import __version__
from app.cli.output import console, emit_json, emit_table, fail, success, warn
from app.core.errors import OsintError
from app.core.logging import configure_logging
from app.models.enums import CaseStatus, Classification, ReportFormat, TargetType

app = typer.Typer(
    name="osint",
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
case_app = typer.Typer(name="case", help="Create and manage investigation cases.")
target_app = typer.Typer(name="target", help="Add and inspect investigation targets.")
app.add_typer(case_app)
app.add_typer(target_app)

JsonOpt = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging.")]


@app.callback()
def main(
    verbose: VerboseOpt = False,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="Override DATABASE_URL.")
    ] = None,
) -> None:
    """Configure logging and the database connection for this invocation."""
    configure_logging(level="DEBUG" if verbose else "WARNING")
    if database_url:
        from app.core.db import configure_engine

        configure_engine(database_url)


@app.command()
def version(as_json: JsonOpt = False) -> None:
    """Print the platform version."""
    if as_json:
        emit_json({"version": __version__})
    else:
        console.print(f"osint-platform {__version__}")


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{label} must be a UUID") from exc


# ------------------------------------------------------------------------ case


@case_app.command("create")
def case_create(
    name: Annotated[str, typer.Argument(help="Case name.")],
    description: Annotated[str | None, typer.Option("--description", "-d")] = None,
    tags: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Create a case."""
    from app.core.db import session_scope
    from app.schemas.case import CaseCreate, CaseRead
    from app.services import cases as service

    with session_scope() as session:
        case = service.create_case(
            session, CaseCreate(name=name, description=description, tags=tags or [])
        )
        payload = CaseRead.model_validate(case).model_dump()

    if as_json:
        emit_json(payload)
    else:
        success(f"Created case [bold]{payload['name']}[/bold]")
        console.print(f"  id: {payload['id']}")


@case_app.command("list")
def case_list(
    status: Annotated[CaseStatus | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    as_json: JsonOpt = False,
) -> None:
    """List cases."""
    from app.core.db import session_scope
    from app.schemas.case import CaseRead
    from app.services import cases as service

    with session_scope() as session:
        items, total = service.list_cases(session, status=status, limit=limit)
        rows = [CaseRead.model_validate(item).model_dump() for item in items]

    if as_json:
        emit_json({"items": rows, "total": total})
        return
    emit_table(
        f"Cases ({total})",
        [{**row, "tags": [tag["name"] for tag in row["tags"]]} for row in rows],
        ["id", "name", "status", "tags", "created_at"],
    )


@case_app.command("show")
def case_show(
    case_id: Annotated[str, typer.Argument(help="Case UUID.")],
    as_json: JsonOpt = False,
) -> None:
    """Show a case with its counters."""
    from app.core.db import session_scope
    from app.schemas.case import CaseRead
    from app.services import cases as service

    with session_scope() as session:
        data = service.case_summary(session, _uuid(case_id, "case_id"))
        payload = {
            **{key: value for key, value in data.items() if key != "case"},
            "case": CaseRead.model_validate(data["case"]).model_dump(),
        }

    if as_json:
        emit_json(payload)
        return
    case = CaseRead.model_validate(data["case"])
    console.print(f"[bold]{case.name}[/bold]  ({case.status})")
    if case.description:
        console.print(f"  {case.description}")
    emit_table(
        "Counters",
        [
            {
                "targets": payload["targets"],
                "findings": payload["findings"],
                "entities": payload["entities"],
                "relationships": payload["relationships"],
                "evidence": payload["evidence"],
            }
        ],
        ["targets", "findings", "entities", "relationships", "evidence"],
    )


@case_app.command("delete")
def case_delete(
    case_id: Annotated[str, typer.Argument(help="Case UUID.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a case and everything it contains."""
    from app.core.db import session_scope
    from app.services import cases as service

    if not yes:
        typer.confirm(f"Delete case {case_id} and all of its data?", abort=True)
    with session_scope() as session:
        service.delete_case(session, _uuid(case_id, "case_id"))
    success(f"Deleted case {case_id}")


# ---------------------------------------------------------------------- target


@target_app.command("add")
def target_add(
    case: Annotated[str, typer.Option("--case", help="Case UUID.")],
    value: Annotated[str | None, typer.Argument(help="Target value.")] = None,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    username: Annotated[str | None, typer.Option("--username")] = None,
    email: Annotated[str | None, typer.Option("--email")] = None,
    url: Annotated[str | None, typer.Option("--url")] = None,
    org: Annotated[str | None, typer.Option("--org", "--organization")] = None,
    ip: Annotated[str | None, typer.Option("--ip")] = None,
    repository: Annotated[str | None, typer.Option("--repo", "--repository")] = None,
    tags: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Add a target to a case.

    Pass the value positionally to let the platform infer its type, or use a
    typed option such as ``--domain`` to be explicit.
    """
    from app.core.db import session_scope
    from app.schemas.case import TargetCreate, TargetRead
    from app.services import cases as service

    typed = _single_target(
        value=value,
        domain=domain,
        username=username,
        email=email,
        url=url,
        org=org,
        ip=ip,
        repository=repository,
    )

    with session_scope() as session:
        target = service.add_target(
            session,
            _uuid(case, "case"),
            TargetCreate(value=typed[0], type=typed[1], tags=tags or []),
        )
        payload = TargetRead.model_validate(target).model_dump()

    if as_json:
        emit_json(payload)
    else:
        success(f"Added {payload['type']} target [bold]{payload['normalized_value']}[/bold]")
        console.print(f"  id: {payload['id']}")


@target_app.command("list")
def target_list(
    case: Annotated[str, typer.Option("--case", help="Case UUID.")],
    as_json: JsonOpt = False,
) -> None:
    """List a case's targets."""
    from app.core.db import session_scope
    from app.schemas.case import TargetRead
    from app.services import cases as service

    with session_scope() as session:
        items, total = service.list_targets(session, _uuid(case, "case"))
        rows = [TargetRead.model_validate(item).model_dump() for item in items]

    if as_json:
        emit_json({"items": rows, "total": total})
        return
    emit_table(f"Targets ({total})", rows, ["id", "type", "normalized_value", "status"])


@app.command("investigate")
def investigate(
    case: Annotated[
        str | None,
        typer.Option("--case", help="Existing case UUID; a new case is created when omitted."),
    ] = None,
    value: Annotated[str | None, typer.Argument(help="Target value.")] = None,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    username: Annotated[str | None, typer.Option("--username")] = None,
    email: Annotated[str | None, typer.Option("--email")] = None,
    url: Annotated[str | None, typer.Option("--url")] = None,
    org: Annotated[str | None, typer.Option("--org", "--organization")] = None,
    ip: Annotated[str | None, typer.Option("--ip")] = None,
    repository: Annotated[str | None, typer.Option("--repo", "--repository")] = None,
    collector: Annotated[
        list[str] | None, typer.Option("--collector", help="Run only these collectors.")
    ] = None,
    exclude_collector: Annotated[
        list[str] | None, typer.Option("--exclude-collector", help="Skip these collectors.")
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Name for a newly created case.")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Run a full investigation against one target.

    Creates a case when ``--case`` is not given, adds the target, runs every
    applicable collector, correlates the results and prints a summary.
    """
    from app.core.db import session_scope
    from app.schemas.case import CaseCreate, TargetCreate
    from app.services import cases as service
    from app.services.engine import InvestigationOptions, run_investigation

    target_value, target_type = _single_target(
        value=value,
        domain=domain,
        username=username,
        email=email,
        url=url,
        org=org,
        ip=ip,
        repository=repository,
    )

    with session_scope() as session:
        if case:
            case_id = _uuid(case, "case")
            service.get_case(session, case_id)
        else:
            created = service.create_case(
                session, CaseCreate(name=name or f"Investigation: {target_value}")
            )
            case_id = created.id

        try:
            service.add_target(session, case_id, TargetCreate(value=target_value, type=target_type))
        except OsintError as exc:
            if exc.code != "conflict":
                raise
        session.commit()

        result = run_investigation(
            session,
            case_id,
            InvestigationOptions(
                include_collectors=list(collector or []),
                exclude_collectors=list(exclude_collector or []),
                on_progress=None if as_json else _print_progress,
            ),
        )
        payload = result.as_dict()

    if as_json:
        emit_json(payload)
        return

    success(f"Investigation complete for [bold]{target_value}[/bold]")
    console.print(f"  case: {payload['case_id']}")
    emit_table(
        "Results",
        [payload],
        [
            "collectors_run",
            "collectors_failed",
            "collectors_skipped",
            "findings_created",
            "entities_created",
            "relationships_created",
            "timeline_events",
        ],
    )
    for note in payload["notes"][:10]:
        console.print(f"  [dim]note:[/dim] {note}")
    for error in payload["errors"][:10]:
        warn(f"{error.get('collector') or error.get('stage')}: {error.get('error')}")


def _print_progress(fraction: float, message: str) -> None:
    console.print(f"  [dim]{fraction:>5.0%}[/dim] {message}")


@app.command("report")
def report(
    case: Annotated[str, typer.Option("--case", help="Case UUID.")],
    report_format: Annotated[
        ReportFormat, typer.Option("--format", "-f", help="Output format.")
    ] = ReportFormat.HTML,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to this file instead of stdout.")
    ] = None,
    max_classification: Annotated[
        Classification,
        typer.Option("--max-classification", help="Withhold content above this level."),
    ] = Classification.PERSONAL,
    min_confidence: Annotated[float, typer.Option("--min-confidence", min=0.0, max=1.0)] = 0.0,
) -> None:
    """Generate an investigation report.

    Every claim in the output cites the stored artefact that supports it.
    """
    from app.core.db import session_scope
    from app.reporting import render_report

    with session_scope() as session:
        body = render_report(
            session,
            _uuid(case, "case"),
            report_format=report_format,
            max_classification=max_classification,
            min_confidence=min_confidence,
        )

    if output is None:
        # The report itself is this command's output, so it goes to stdout raw.
        print(body)
        return
    output.write_text(body, encoding="utf-8")
    success(f"Wrote {report_format.value} report to [bold]{output}[/bold] ({len(body)} bytes)")


@app.command("collectors")
def list_collectors(as_json: JsonOpt = False) -> None:
    """List the available collectors and whether they can currently run."""
    from app.collectors.registry import collector_metadata, load_builtin_collectors

    load_builtin_collectors()
    entries = collector_metadata()
    if as_json:
        emit_json(entries)
        return
    emit_table(
        "Collectors",
        [
            {**entry, "supported_targets": ", ".join(entry["supported_targets"])}
            for entry in entries
        ],
        ["name", "version", "supported_targets", "requires_api_key", "available"],
    )


@app.command("normalize")
def normalize(
    value: Annotated[str, typer.Argument(help="Raw target value.")],
    as_json: JsonOpt = False,
) -> None:
    """Show how a raw input would be normalised, without storing anything."""
    from app.services.normalization import normalize_target

    target = normalize_target(value)
    payload = {
        "raw_input": target.raw_input,
        "type": target.type.value,
        "normalized_value": target.value,
        "attributes": target.attributes,
    }
    if as_json:
        emit_json(payload)
    else:
        emit_table("Normalisation", [payload], ["raw_input", "type", "normalized_value"])


def _single_target(**kwargs: str | None) -> tuple[str, TargetType | None]:
    """Resolve exactly one of the typed target options into ``(value, type)``."""
    mapping: dict[str, TargetType | None] = {
        "value": None,
        "domain": TargetType.DOMAIN,
        "username": TargetType.USERNAME,
        "email": TargetType.EMAIL,
        "url": TargetType.URL,
        "org": TargetType.ORGANIZATION,
        "ip": TargetType.IP,
        "repository": TargetType.REPOSITORY,
    }
    supplied = [(key, value) for key, value in kwargs.items() if value]
    if not supplied:
        raise typer.BadParameter(
            "Provide a target value, or one of --domain/--username/--email/--url/"
            "--org/--ip/--repo"
        )
    if len(supplied) > 1:
        raise typer.BadParameter(
            f"Provide only one target, got: {', '.join(key for key, _ in supplied)}"
        )
    key, value = supplied[0]
    return value, mapping[key]


def run() -> None:
    """Entry point that converts domain errors into clean CLI failures."""
    try:
        app()
    except OsintError as exc:
        fail(f"{exc.code}: {exc.message}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":  # pragma: no cover
    run()
