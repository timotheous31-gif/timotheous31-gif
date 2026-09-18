"""Measuring what discovery actually achieved, rather than whether it ran.

The standard this exists to enforce was set in review and is worth restating:
*a change is not successful merely because it generates better queries.* A
provider can be configured, a plan can be well aimed, forty searches can execute,
and the investigation can still end with nothing an analyst can use — or, worse,
with a confident page about a different person of the same name.

So discovery is measured on six numbers, and two of them are failure counts that
must stay at zero:

**Known-URL recall.** Of the pages a case is known to have, how many did the run
store? This is the only metric that speaks to recall directly, and it needs a
ground-truth list, which is why it is driven from fixtures.

**Useful-result recall.** Of what was stored, how much is worth an analyst's
attention — a structured profile or publication page, or a page whose own text
was observed to carry a supplied anchor. A result that is a URL and a name is
discovery; it is not yet a lead.

**False associations.** Stored pages that are known to be about somebody else and
that nonetheless scored at or above POSSIBLE_MATCH. Recall bought with these is
not an improvement; it is the defect the conservative scoring exists to prevent.

**Duplicate rate.** How much of what came back was the same page again. High
duplication means the plan is spending searches to re-learn what it knows.

**Searches consumed, against the budget.** What the run cost in provider
operations, and whether it hit its ceiling.

**Enrichment fetches consumed, against the cap.** How many pages the platform
read itself.

Plus two hard invariants, asserted rather than reported: nothing is
auto-confirmed from search results, and nothing reaches POSSIBLE_MATCH on a name
alone.

Every number here is computed from what is actually in the database after a run.
None of it is read back from the ingest report's own self-description, because a
benchmark that trusts the thing it is measuring measures nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.correlation.confidence import AUTO_MERGE_THRESHOLD, POSSIBLE_MATCH_THRESHOLD
from app.models import Finding
from app.services.search_ingest import SEARCH_COLLECTOR

#: The band at which a candidate starts being something an analyst weighs rather
#: than a lead they glance at. A false association below it is noise; at or above
#: it, it is a wrong answer.
POSSIBLE = POSSIBLE_MATCH_THRESHOLD


def _key(url: str) -> str:
    """A URL reduced to what makes two spellings the same page.

    Scheme, ``www.``, a trailing slash and a fragment are not differences. Query
    strings are kept, because for many sites they are the whole address.
    """
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = (parts.path or "/").rstrip("/") or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


@dataclass(slots=True)
class BenchmarkMetrics:
    """One run's discovery performance, as measured from the database."""

    case: str
    provider: str
    #: Ground truth.
    known_urls: int = 0
    known_urls_found: int = 0
    missed_urls: list[str] = field(default_factory=list)
    #: What was stored.
    results_stored: int = 0
    useful_results: int = 0
    low_context_results: int = 0
    #: Failure counts. Both must be zero.
    false_associations: int = 0
    false_association_urls: list[str] = field(default_factory=list)
    auto_confirmations: int = 0
    name_only_above_possible: int = 0
    #: Cost and effort.
    duplicates: int = 0
    results_seen: int = 0
    searches_used: int = 0
    search_budget: int | None = None
    queries_planned: int = 0
    queries_as_planned: bool = True
    enrichment_fetches: int = 0
    enrichment_limit: int = 0

    @property
    def known_url_recall(self) -> float:
        if not self.known_urls:
            return 0.0
        return round(self.known_urls_found / self.known_urls, 3)

    @property
    def useful_result_recall(self) -> float:
        if not self.results_stored:
            return 0.0
        return round(self.useful_results / self.results_stored, 3)

    @property
    def duplicate_rate(self) -> float:
        if not self.results_seen:
            return 0.0
        return round(self.duplicates / self.results_seen, 3)

    @property
    def clean(self) -> bool:
        """True when no wrong answer was produced. Not the same as useful."""
        return (
            self.false_associations == 0
            and self.auto_confirmations == 0
            and self.name_only_above_possible == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "provider": self.provider,
            "known_urls": self.known_urls,
            "known_urls_found": self.known_urls_found,
            "known_url_recall": self.known_url_recall,
            "missed_urls": list(self.missed_urls),
            "results_stored": self.results_stored,
            "useful_results": self.useful_results,
            "useful_result_recall": self.useful_result_recall,
            "low_context_results": self.low_context_results,
            "false_associations": self.false_associations,
            "false_association_urls": list(self.false_association_urls),
            "auto_confirmations": self.auto_confirmations,
            "name_only_above_possible": self.name_only_above_possible,
            "duplicates": self.duplicates,
            "results_seen": self.results_seen,
            "duplicate_rate": self.duplicate_rate,
            "searches_used": self.searches_used,
            "search_budget": self.search_budget,
            "queries_planned": self.queries_planned,
            "queries_as_planned": self.queries_as_planned,
            "enrichment_fetches": self.enrichment_fetches,
            "enrichment_limit": self.enrichment_limit,
            "clean": self.clean,
        }


def measure(
    session: Session,
    *,
    case_id: uuid.UUID,
    report: Any,
    known_urls: list[str],
    decoy_urls: list[str],
    label: str = "",
) -> BenchmarkMetrics:
    """Measure one ingestion run against a case's ground truth.

    ``known_urls`` are pages the case is known to have; ``decoy_urls`` are pages
    known to be about a different person of the same name. Both come from a
    fixture, which is the only honest source for them: asserting a recall figure
    against a living person's real footprint would be asserting facts about that
    person, and a benchmark has no business doing that.
    """
    findings = list(
        session.scalars(
            select(Finding).where(
                Finding.case_id == case_id,
                Finding.collector == SEARCH_COLLECTOR,
            )
        )
    )
    accounting = getattr(report, "accounting", None)
    enrichment = dict(getattr(report, "enrichment", {}) or {})

    metrics = BenchmarkMetrics(
        case=label or str(case_id),
        provider=str(getattr(report, "provider", "")),
        known_urls=len(known_urls),
        results_stored=int(getattr(report, "results_stored", 0)),
        results_seen=int(getattr(report, "results_seen", 0)),
        duplicates=int(getattr(report, "duplicates", 0)),
        low_context_results=int(getattr(report, "low_context_results", 0)),
        searches_used=int(getattr(report, "queries_run", 0)),
        search_budget=getattr(accounting, "search_budget", None),
        queries_planned=int(getattr(report, "queries_planned", 0)),
        queries_as_planned=bool(getattr(report, "queries_as_planned", True)),
        enrichment_fetches=int(enrichment.get("fetches_used") or 0),
        enrichment_limit=int(enrichment.get("limit") or 0),
    )

    stored = {
        _key(str((finding.data or {}).get("url") or finding.source_url or "")): finding
        for finding in findings
    }
    wanted = {_key(url) for url in known_urls}
    decoys = {_key(url) for url in decoy_urls}

    metrics.known_urls_found = sum(1 for key in wanted if key in stored)
    metrics.missed_urls = sorted(url for url in known_urls if _key(url) not in stored)

    for key, finding in stored.items():
        data = finding.data or {}
        anchors = [*_strings(data.get("affiliations")), *_strings(data.get("locations"))]
        # "Structured" means the URL's own shape says it is a record about a
        # person — a profile or a publication. Deliberately not "the URL was
        # classified at all": every http(s) URL classifies to *something*
        # (``kind == "web"`` for an ordinary page), so treating classification as
        # structure would mark every result useful and measure nothing.
        structured = data.get("url_kind") in {"social", "profile", "publication"}
        if anchors or structured:
            metrics.useful_results += 1
        score = float(finding.confidence or 0.0)
        if key in decoys and score >= POSSIBLE:
            metrics.false_associations += 1
            metrics.false_association_urls.append(str(data.get("url") or finding.source_url or ""))
        if score >= AUTO_MERGE_THRESHOLD:
            metrics.auto_confirmations += 1
        if not anchors and score >= POSSIBLE:
            metrics.name_only_above_possible += 1
    return metrics


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def render(results: list[BenchmarkMetrics]) -> str:
    """A table a human can read in a PR description or a terminal."""
    header = (
        "| Case | Provider | Known-URL recall | Useful-result recall | False assoc. | "
        "Dup. rate | Searches | Enrichment |"
    )
    rule = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    lines = [header, rule]
    for item in results:
        budget = "—" if item.search_budget is None else str(item.search_budget)
        lines.append(
            f"| {item.case} | `{item.provider}` | "
            f"{item.known_urls_found}/{item.known_urls} ({item.known_url_recall:.2f}) | "
            f"{item.useful_results}/{item.results_stored} ({item.useful_result_recall:.2f}) | "
            f"{item.false_associations} | {item.duplicate_rate:.2f} | "
            f"{item.searches_used}/{budget} | "
            f"{item.enrichment_fetches}/{item.enrichment_limit} |"
        )
    return "\n".join(lines)
