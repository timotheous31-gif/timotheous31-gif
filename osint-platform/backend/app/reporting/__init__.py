"""Report generation."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models.enums import Classification, ReportFormat
from app.reporting.model import ReportModel, build_report
from app.reporting.renderers import (
    RENDERERS,
    render_html,
    render_json,
    render_markdown,
)

__all__ = [
    "RENDERERS",
    "ReportModel",
    "build_report",
    "render_html",
    "render_json",
    "render_markdown",
    "render_report",
]


def render_report(
    session: Session,
    case_id: uuid.UUID,
    *,
    report_format: ReportFormat = ReportFormat.HTML,
    max_classification: Classification = Classification.PERSONAL,
    min_confidence: float = 0.0,
) -> str:
    """Build and render a case report in ``report_format``."""
    renderer = RENDERERS.get(str(report_format))
    if renderer is None:
        raise ValidationError(f"Unsupported report format {report_format!r}")
    model = build_report(
        session,
        case_id,
        max_classification=max_classification,
        min_confidence=min_confidence,
    )
    return str(renderer(model))
