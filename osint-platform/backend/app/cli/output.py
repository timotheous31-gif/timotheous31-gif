"""CLI rendering helpers: rich tables for humans, JSON for pipelines."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()
error_console = Console(stderr=True)


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID | datetime):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(item) for item in value]
    return value


def emit_json(payload: Any) -> None:
    """Print ``payload`` as JSON on stdout."""
    console.print_json(json.dumps(_plain(payload), default=str))


def emit_table(title: str, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> None:
    """Print ``rows`` as a table with the given ``columns``."""
    table = Table(title=title, header_style="bold", show_lines=False)
    for column in columns:
        table.add_column(column.replace("_", " ").title(), overflow="fold")
    count = 0
    for row in rows:
        table.add_row(*[_format_cell(row.get(column)) for column in columns])
        count += 1
    if count == 0:
        console.print(f"[dim]{title}: nothing to show[/dim]")
        return
    console.print(table)


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, list | tuple):
        return ", ".join(str(_plain(item)) for item in value) or "-"
    return str(value)


def success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    error_console.print(f"[yellow]![/yellow] {message}")


def fail(message: str) -> None:
    error_console.print(f"[red]✗[/red] {message}")
