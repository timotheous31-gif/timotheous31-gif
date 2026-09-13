"""Report generation endpoint.

The most sensitive route in the platform: it renders a case's whole investigation
into one document, and before this change anyone holding a case UUID could fetch
it anonymously. Three things now protect it.

**Authorization.** ``REPORT_EXPORT`` on a case the caller's workspace owns. A
VIEWER may export — reading is the role's purpose — but a non-member gets a 404.

**HTML reports are downloads, not pages.** A report is built from titles,
snippets and page text collected from the open web, which is attacker-influenced
by definition. Jinja autoescapes it and a test holds that, but autoescaping is one
mistake away from failing, and this response is served from the API's own origin —
where the session cookie lives. So the HTML report goes out as an attachment under
a restrictive CSP, and a cross-site-scripting bug in the template becomes a bad
downloaded file rather than a session theft. The frontend renders Markdown and
JSON itself, on its own origin, and does not need the HTML variant inline.

**Every export is recorded.** Exporting is how an investigation leaves the
platform, so a pilot customer asking "who took this out, and when" gets an answer.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import AppSettings, CaseContext, DbSession, require
from app.core import throttle
from app.core.errors import ThrottledError
from app.core.permissions import Permission
from app.models.enums import AuditEvent, Classification, ReportFormat
from app.services import audit

router = APIRouter(prefix="/cases/{case_id}", tags=["reports"])

MEDIA_TYPES = {
    ReportFormat.HTML: "text/html; charset=utf-8",
    ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ReportFormat.JSON: "application/json",
}

#: Content-Security-Policy for a rendered report. The document is self-contained
#: — inline styles, no scripts, no external assets — so everything else can be
#: denied outright. ``sandbox`` is the belt to that pair of braces: even if markup
#: got through the escaping, it would run with no origin, no scripts and no forms.
REPORT_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "sandbox"
)

#: Characters that have no business in a filename. Everything else is stripped
#: rather than escaped, because a case name is investigator-supplied text and a
#: quote or a newline in a Content-Disposition header is a header-injection
#: primitive.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

EXTENSIONS = {
    ReportFormat.HTML: "html",
    ReportFormat.MARKDOWN: "md",
    ReportFormat.JSON: "json",
}


def _filename(case_name: str, report_format: ReportFormat) -> str:
    """A safe download filename derived from the case name."""
    stem = _UNSAFE_FILENAME.sub("-", (case_name or "case").strip()).strip("-")[:60]
    return f"{stem or 'case'}-report.{EXTENSIONS[report_format]}"


@router.get("/report", summary="Generate an investigation report")
def get_report(
    ctx: Annotated[CaseContext, Depends(require(Permission.REPORT_EXPORT))],
    session: DbSession,
    settings: AppSettings,
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

    verdict = throttle.check(
        throttle.principal_key("report", str(ctx.principal.user_id)),
        limit=settings.rate_limit_report_per_hour,
        window_seconds=3600,
        settings=settings,
    )
    if verdict.refused:
        raise ThrottledError(
            "You have generated a lot of reports in a short time. Rendering one reads "
            "the whole case, so there is an hourly ceiling. Try again shortly.",
            retry_after=verdict.retry_after,
        )

    body = render_report(
        session,
        ctx.case_id,
        report_format=report_format,
        max_classification=max_classification,
        min_confidence=min_confidence,
        embed_images=embed_images,
        execution=execution,
    )
    audit.record(
        session,
        event=AuditEvent.REPORT_GENERATED,
        actor_user_id=ctx.principal.user_id,
        workspace_id=ctx.workspace_id,
        object_type="case",
        object_id=ctx.case_id,
        metadata={
            "format": str(report_format),
            "max_classification": str(max_classification),
            "execution": str(execution) if execution else None,
        },
    )
    session.commit()

    headers = {
        # Never rendered as a page on the API's origin. See the module docstring.
        "Content-Disposition": (
            f'attachment; filename="{_filename(ctx.case.name, report_format)}"'
        ),
        "Content-Security-Policy": REPORT_CSP,
        "X-Content-Type-Options": "nosniff",
        # A report is a person's investigation file. It must not sit in a shared
        # cache, and a browser's back button should re-authorize rather than
        # re-serve it.
        "Cache-Control": "no-store, private",
        "Referrer-Policy": "no-referrer",
    }
    return Response(content=body, media_type=MEDIA_TYPES[report_format], headers=headers)
