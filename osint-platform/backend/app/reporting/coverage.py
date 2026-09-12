"""What was searched, what was not, and why — as distinct facts.

The failure this exists to prevent is the worst kind a report can contain. An
investigation for a person with a substantial public footprint produced "no
findings", and a reader would reasonably take that to mean *there is nothing to
find*. What actually happened was that the public web was never searched: no
provider was configured, so the only channel that would have found her was
never opened.

"Nothing was found" and "nobody looked" are different claims, and a report that
cannot tell them apart is worse than no report. So every source family resolves
to exactly one state, each state says plainly what it means, and the executive
summary is forbidden from describing an unsearched channel as an absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.collectors.capabilities import CAPABILITIES
from app.models.enums import RunStatus


class CoverageState(StrEnum):
    """The state of one source family in one investigation."""

    #: Searched, and it returned something.
    FOUND = "FOUND"
    #: Searched, and it genuinely returned nothing. The only state that is
    #: evidence of absence — and only for what that source indexes.
    NO_MATCH_RETURNED = "NO_MATCH_RETURNED"
    #: Never attempted in this investigation. Says nothing about the subject.
    NOT_SEARCHED = "NOT_SEARCHED"
    #: Attempted and declined — rate-limited, or the platform refuses anonymous
    #: automated access. Not an absence.
    SKIPPED = "SKIPPED"
    #: Attempted and broke. An upstream failure, not a negative result.
    FAILED = "FAILED"
    #: Not automatable, but the investigator can search it by hand and import
    #: what they find. A gap with a workflow, not a dead end.
    MANUAL_REVIEW_AVAILABLE = "MANUAL_REVIEW_AVAILABLE"
    #: A channel that would work, switched off by configuration. The Tabitha
    #: case in one value: the public web was not searched because no provider
    #: was configured, which is a fact about the deployment, not the subject.
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"


#: One sentence per state, written for the person reading the report.
STATE_MEANINGS: dict[CoverageState, str] = {
    CoverageState.FOUND: "Searched; public records were returned.",
    CoverageState.NO_MATCH_RETURNED: (
        "Searched; this source returned nothing. Evidence of absence in this source only."
    ),
    CoverageState.NOT_SEARCHED: (
        "Not searched in this investigation. This is not evidence that nothing exists."
    ),
    CoverageState.SKIPPED: (
        "Declined to answer an anonymous automated request. Not evidence of absence."
    ),
    CoverageState.FAILED: ("The source failed with an upstream error. Not evidence of absence."),
    CoverageState.MANUAL_REVIEW_AVAILABLE: (
        "Cannot be queried automatically. Search it yourself and import what you find; "
        "the platform generates the queries."
    ),
    CoverageState.PROVIDER_NOT_CONFIGURED: (
        "Not searched because no search provider is configured. Configure one to search "
        "the public web automatically. This is not evidence that nothing exists."
    ),
}

#: States that are safe to describe as an absence. Deliberately one value.
ABSENCE_STATES = frozenset({CoverageState.NO_MATCH_RETURNED})

#: States that represent a gap a reader must be told about.
GAP_STATES = frozenset(
    {
        CoverageState.NOT_SEARCHED,
        CoverageState.SKIPPED,
        CoverageState.FAILED,
        CoverageState.MANUAL_REVIEW_AVAILABLE,
        CoverageState.PROVIDER_NOT_CONFIGURED,
    }
)


@dataclass(slots=True)
class CoverageItem:
    """One source family, and what this investigation can honestly say about it."""

    source: str
    display_name: str
    state: CoverageState
    detail: str = ""
    findings: int = 0
    runs: int = 0

    @property
    def is_gap(self) -> bool:
        return self.state in GAP_STATES

    @property
    def supports_absence(self) -> bool:
        return self.state in ABSENCE_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "state": str(self.state),
            "meaning": STATE_MEANINGS[self.state],
            "detail": self.detail or None,
            "findings": self.findings,
            "runs": self.runs,
            "is_gap": self.is_gap,
        }


def state_for_run(status: RunStatus, findings: int) -> CoverageState:
    """The coverage state one collector's run implies."""
    if status is RunStatus.SKIPPED:
        return CoverageState.SKIPPED
    if status in {RunStatus.FAILED, RunStatus.TIMEOUT}:
        return CoverageState.FAILED
    if status in {RunStatus.SUCCESS, RunStatus.PARTIAL}:
        return CoverageState.FOUND if findings else CoverageState.NO_MATCH_RETURNED
    # PENDING or RUNNING: it has not answered, so nothing may be concluded.
    return CoverageState.NOT_SEARCHED


def manual_only_platforms() -> list[CoverageItem]:
    """Platforms the capability registry says cannot be queried automatically.

    Read from the registry rather than listed here, so a platform added there
    appears in the report's coverage table without this module learning its name.
    """
    return [
        CoverageItem(
            source=capability.platform,
            display_name=capability.display_name,
            state=CoverageState.MANUAL_REVIEW_AVAILABLE,
            detail=capability.notes or capability.fetch_note,
        )
        for capability in CAPABILITIES
        if not capability.handle_check_supported and not capability.server_fetchable
    ]


def web_search_coverage(
    *,
    provider: str,
    configured: bool,
    queries_run: int,
    results_stored: int,
    reason: str = "",
    failures: int = 0,
    blocked: bool = False,
    outcome: str = "ok",
    queries_as_planned: bool = True,
    search_budget: int | None = None,
) -> CoverageItem:
    """The public-web channel, stated honestly.

    Each outcome is a different fact and gets a different state: no provider
    configured, a provider that ran and found things, a provider that ran and
    found nothing, a provider that broke, and — the case this platform had to
    learn — a provider that was *stopped*.

    ``blocked`` is the last of those. A budget that ran out, a rate limit, or an
    upstream error arriving inside an HTTP 200 all mean the public web was not
    fully examined. None of them is an absence, so none of them may become
    ``NO_MATCH_RETURNED``: with nothing stored they are ``SKIPPED``, and with
    something stored the coverage is partial and says so.
    """
    if not configured:
        return CoverageItem(
            source="public_web",
            display_name="Public web search",
            state=CoverageState.PROVIDER_NOT_CONFIGURED,
            detail=reason or "SEARCH_PROVIDER is not set.",
        )
    if queries_run == 0:
        if blocked:
            return CoverageItem(
                source="public_web",
                display_name="Public web search",
                state=CoverageState.SKIPPED,
                detail=(
                    f"{provider} was configured but ran no search: {reason or outcome}. "
                    f"Nothing is known about what the public web holds."
                ),
            )
        return CoverageItem(
            source="public_web",
            display_name="Public web search",
            state=CoverageState.FAILED if failures else CoverageState.NOT_SEARCHED,
            detail=(
                f"{provider} was configured but every query failed."
                if failures
                else f"{provider} was configured but no query ran."
            ),
        )

    noun = "search(es)" if not queries_as_planned else "query(ies)"
    detail = f"{queries_run} {noun} through {provider}; {results_stored} result(s) stored."
    if not queries_as_planned:
        detail += (
            " This provider chooses its own queries from the recon plan rather than "
            "running them verbatim, so the searches recorded are the ones it ran."
        )
    if search_budget is not None:
        detail += f" Search budget for this execution: {queries_run} of {search_budget}."
    if blocked:
        detail += (
            f" Coverage is partial: the channel was stopped before it finished "
            f"({reason or outcome})."
        )
        return CoverageItem(
            source="public_web",
            display_name="Public web search",
            state=CoverageState.FOUND if results_stored else CoverageState.SKIPPED,
            detail=detail,
            findings=results_stored,
            runs=queries_run,
        )
    return CoverageItem(
        source="public_web",
        display_name="Public web search",
        state=CoverageState.FOUND if results_stored else CoverageState.NO_MATCH_RETURNED,
        detail=detail,
        findings=results_stored,
        runs=queries_run,
    )


def image_search_coverage(*, images: int, image_queries_run: int) -> CoverageItem:
    """Public images, which are never searched by likeness.

    Image *search* here means finding pages that publish a picture next to a
    name. The platform looks up no picture by its content and compares no two
    pictures, so this channel is manual unless a provider returns image results
    of its own.

    ``image_queries_run`` is the number of image-shaped queries a provider
    actually answered, and only a number above zero earns
    ``NO_MATCH_RETURNED``. A configured provider is not a search: this said
    "no public image was returned for the generated image queries" whenever one
    was configured, whether or not a single image query had been issued — an
    absence claim nobody had verified, produced by the module written to stop
    exactly that.
    """
    if images:
        return CoverageItem(
            source="public_images",
            display_name="Public images",
            state=CoverageState.FOUND,
            detail=f"{images} public image(s) recorded as page context.",
            findings=images,
        )
    if image_queries_run > 0:
        return CoverageItem(
            source="public_images",
            display_name="Public images",
            state=CoverageState.NO_MATCH_RETURNED,
            detail=(
                f"{image_queries_run} image query(ies) ran and returned no public image. "
                "Searched by name only — never by the content of a picture."
            ),
        )
    return CoverageItem(
        source="public_images",
        display_name="Public images",
        state=CoverageState.MANUAL_REVIEW_AVAILABLE,
        detail=(
            "Not searched automatically. The platform generates image queries for you "
            "to run by name; it never looks a picture up by its content and never "
            "compares one picture with another."
        ),
    )


def summarise_gaps(items: list[CoverageItem]) -> list[str]:
    """Sentences the executive summary must carry when channels went unsearched.

    The point of returning these rather than a count: a summary saying "12
    sources searched" beside "no findings" still misleads, and the only fix is
    to name the channels that were not searched in the same breath.
    """
    gaps = [item for item in items if item.is_gap]
    if not gaps:
        return []
    lines = []
    not_searched = [item for item in gaps if item.state is CoverageState.PROVIDER_NOT_CONFIGURED]
    if not_searched:
        lines.append(
            "The public web was not searched in this investigation: no search provider is "
            "configured. Absence of findings here is not evidence that nothing exists."
        )
    manual = [item for item in gaps if item.state is CoverageState.MANUAL_REVIEW_AVAILABLE]
    if manual:
        names = ", ".join(sorted(item.display_name for item in manual))
        lines.append(
            f"{names} cannot be queried automatically and were not searched by the platform. "
            f"Generated queries are available for you to run and import."
        )
    broken = [item for item in gaps if item.state is CoverageState.FAILED]
    if broken:
        names = ", ".join(sorted(item.display_name for item in broken))
        lines.append(
            f"{names} failed with an upstream error. That is a gap in coverage, not a "
            f"negative result."
        )
    declined = [item for item in gaps if item.state is CoverageState.SKIPPED]
    # The public-web channel is separated out because the generic sentence is
    # wrong for it. A search provider that hit its budget or was rate-limited did
    # not "decline an anonymous request" — it was stopped part-way, and the
    # honest remedy is the manual plan rather than a silent switch to another
    # paid provider.
    web = [item for item in declined if item.source == "public_web"]
    if web:
        detail = next((item.detail for item in web if item.detail), "")
        lines.append(
            "The public web channel was stopped before it finished, so the public web was "
            "examined incompletely. "
            + (f"{detail} " if detail else "")
            + "No other paid provider was substituted. The generated queries remain "
            "available to run by hand and import."
        )
    rest = [item for item in declined if item.source != "public_web"]
    if rest:
        names = ", ".join(sorted(item.display_name for item in rest))
        lines.append(
            f"{names} declined an anonymous automated request. Nothing follows about the "
            f"subject from that."
        )
    return lines
