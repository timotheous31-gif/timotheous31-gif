"""Report generation endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response

from app.api.deps import CaseId, DbSession
from app.models.enums import Classification, ReportFormat

router = APIRouter(prefix="/cases/{case_id}", tags=["reports"])

MEDIA_TYPES = {
    ReportFormat.HTML: "text/html; charset=utf-8",
    ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ReportFormat.JSON: "application/json",
}


@router.get("/report", summary="Generate an investigation report")
def get_report(
    case_id: CaseId,
    session: DbSession,
    report_format: ReportFormat = Query(default=ReportFormat.HTML, alias="format"),
    max_classification: Classification = Query(
        default=Classification.PERSONAL,
        description="Content above this classification is withheld from the report.",
    ),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    embed_images: bool = Query(
        default=False,
        description=(
            "Markdown only: draw render-safe image evidence inline. Off by default "
            "because a remote image in an exported document makes the reader's viewer "
            "fetch a third-party URL, disclosing when and from where the report was "
            "opened. The image card always carries the URL and full provenance."
        ),
    ),
    execution: uuid.UUID | None = Query(
        default=None,
        description=(
            "A job id. Renders that execution's report — what it observed, from its own "
            "immutable observation records — instead of the current state of the case. A "
            "later execution cannot change it: evidence and scores discovered afterwards "
            "are absent by construction. Omit for the current case report."
        ),
    ),
) -> Response:
    """Render the case as a report, or one execution of it.

    Every claim in the output links to the evidence that supports it. The
    export privacy policy defaults to withholding anything above PERSONAL, so a
    report can be circulated more widely than the case database itself.

    With ``execution`` set the report is forensically scoped: it renders the
    observation snapshots that execution recorded, which makes a historical report
    stable across later reruns.
    """
    from app.reporting import render_report

    body = render_report(
        session,
        case_id,
        report_format=report_format,
        max_classification=max_classification,
        min_confidence=min_confidence,
        embed_images=embed_images,
        execution=execution,
    )
    return Response(content=body, media_type=MEDIA_TYPES[report_format])
