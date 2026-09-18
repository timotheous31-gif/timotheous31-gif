"""Turning public search results into correlated, reportable evidence.

The gap this closes came from a real acceptance test. The platform generated
forty-six well-aimed Google queries for a person with a substantial public
professional footprint, and then produced zero findings — because a query is a
suggestion, and nothing came back unless the investigator pasted results in by
hand. That is not an investigation product.

So when a legitimate search provider is configured, its results run the *same*
pipeline a collector's do:

    SearchResult -> URL validation -> platform classification -> candidate
    finding -> anchor correlation -> social / web / document / image evidence
    -> report

Five rules shape it.

**A search result is discovery evidence, not identity proof.** Nothing gets a
confidence boost for having been returned by a search engine. The name rule that
fires depends on the *spelling the page itself displays*, so a reduced-name hit
stays weak until an anchor corroborates it.

**The same page found four ways is still one page.** A URL reached from two
queries and two name variants is one source with four provenance records, not
four agreeing sources. Corroboration counts sources, and a search engine
repeating itself is not a second source. This holds across providers too: a
second discovery channel reaching the same URL is a second route, not a second
party, which is why every search channel is a relay source in the lineage model.

**What was planned and what was executed are different facts.** Most providers
run the query they are handed. One — Anthropic's server-side web search — decides
its own queries from a brief, and has no parameter that would accept an exact
one. Both are supported, and the record says which happened: a planned query is
never written down as an executed one, and the execution ledger counts searches
the provider reported running, not queries the platform hoped it would run.

**A missing field is never filled in.** A provider that returns no description
produces a result whose snippet is ``None``, and that result is stored as a
low-context discovery candidate: correlated on its displayed name, with the
report saying so. The platform may then read a *small, capped* number of the most
promising pages itself, through the existing SSRF-guarded client, and correlate
against what those pages actually say. What it never does is synthesise a
description — not from the URL, not from the title, not from a model's summary.

**Provider output is untrusted input.** Every URL is validated by the existing
SSRF guard before it is stored or fetched, and nothing here opens a second HTTP
path — the provider call and any page fetch go through ``app.core.http``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.person import PersonCandidate, PersonContext, assess
from app.collectors.social import SocialProfile, classify_url
from app.core.errors import ConfigurationError, SSRFError
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.core.ssrf import validate_url
from app.correlation.confidence import default_engine
from app.models import Case, Finding, Target
from app.models.enums import Classification, FindingKind, TargetType
from app.services.enrichment import EnrichmentBudget, PageExcerpt, fetch_excerpt
from app.services.evidence import EvidenceStore
from app.services.name_variants import generate_variants
from app.services.normalization import NormalizedTarget
from app.services.providers.search import (
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_OK,
    OUTCOME_RATE_LIMITED,
    OUTCOME_UNAVAILABLE,
    ProviderAccounting,
    SearchBatch,
    SearchBrief,
    SearchProvider,
    SearchResult,
    get_search_provider,
    planned_queries,
)
from app.services.recon import FAMILY_IMAGE, staged_plan

log = get_logger(__name__)

#: The collector identity automated search results are filed under. Distinct
#: from ``search`` (the older single-query collector) and from
#: ``manual_search_recon``, so a report can tell the three apart at a glance.
SEARCH_COLLECTOR = "provider_search"
SEARCH_SOURCE_LABEL = "Public web search"
EVIDENCE_CLASS = "provider_search_result"

#: Queries offered to the provider per ingestion run, and results taken from
#: each. Bounded because a provider costs money per call and an investigator reads
#: a worklist, not a corpus.
MAX_QUERIES = 12
MAX_RESULTS_PER_QUERY = 10

#: Hosts whose results are pages about the *search*, not about the subject.
NOISE_HOSTS = frozenset(
    {
        "google.com",
        "bing.com",
        "duckduckgo.com",
        "webcache.googleusercontent.com",
        "translate.google.com",
        "policies.google.com",
        "support.google.com",
    }
)

#: Outcomes that mean the channel could not do its job, as opposed to doing it
#: and finding nothing. Never collapsed into "no match returned".
BLOCKED_OUTCOMES = frozenset({OUTCOME_BUDGET_EXHAUSTED, OUTCOME_RATE_LIMITED, OUTCOME_UNAVAILABLE})


@dataclass(slots=True)
class IngestReport:
    """What one ingestion run did, for the API and the report's coverage table."""

    provider: str
    configured: bool
    reason: str = ""
    #: Queries the recon plan offered the provider. A *plan*, not a record of
    #: work: kept separate from what ran so neither can be read as the other.
    queries_planned: int = 0
    #: Search operations the provider reported executing. For a provider that
    #: runs what it is handed this equals the number of planned queries that
    #: succeeded; for a model-mediated provider it is its own count, which is
    #: also what it bills.
    queries_run: int = 0
    #: The query text the provider actually executed, in order, deduplicated.
    executed_queries: list[str] = field(default_factory=list)
    #: False where the provider chose its own queries.
    queries_as_planned: bool = True
    #: Of the executed searches, how many were image-shaped. Counted separately
    #: because the report's image channel must not claim "searched, nothing
    #: returned" on the strength of a provider merely being configured: that is
    #: an absence nobody verified, which is the one thing the coverage model
    #: exists to prevent. A provider that chooses its own queries cannot attribute
    #: a search to the image family, so this stays zero and the channel correctly
    #: reports itself unverified.
    image_queries_run: int = 0
    results_seen: int = 0
    results_stored: int = 0
    duplicates: int = 0
    rejected_urls: int = 0
    #: Results stored with no provider description at all. They correlate on the
    #: displayed name unless enrichment read the page.
    low_context_results: int = 0
    #: What the channel cost and what it was allowed to spend. An estimate.
    accounting: ProviderAccounting | None = None
    #: ``ok`` or the reason the channel could not finish.
    outcome: str = OUTCOME_OK
    #: Bounded page reads, and every decision not to read one.
    enrichment: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    #: Every finding this run saw, stored or already present. What promotion works
    #: from, because a page re-seen with new anchors has to be rescored.
    findings: list[uuid.UUID] = field(default_factory=list)
    #: Only the ones this run *created*. Separate because "observed" and "first
    #: observed" are different facts, and the execution ledger records both.
    new_findings: list[uuid.UUID] = field(default_factory=list)

    @property
    def searched(self) -> bool:
        return self.queries_run > 0

    @property
    def blocked(self) -> bool:
        """True when the channel was stopped rather than simply finding nothing."""
        return self.outcome in BLOCKED_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "reason": self.reason or None,
            "queries_planned": self.queries_planned,
            "queries_run": self.queries_run,
            "executed_queries": list(self.executed_queries),
            "queries_as_planned": self.queries_as_planned,
            "image_queries_run": self.image_queries_run,
            "results_seen": self.results_seen,
            "results_stored": self.results_stored,
            "duplicates": self.duplicates,
            "rejected_urls": self.rejected_urls,
            "low_context_results": self.low_context_results,
            "outcome": self.outcome,
            "accounting": self.accounting.to_dict() if self.accounting else None,
            "enrichment": dict(self.enrichment),
            "failures": list(self.failures),
            "findings": [str(item) for item in self.findings],
            "new_findings": [str(item) for item in self.new_findings],
        }


def public_url(raw: str) -> str | None:
    """Validate a provider-supplied URL, or return ``None``.

    Held to exactly the standard a pasted URL is: http(s) only, no credentials,
    and the SSRF guard's verdict on the address. A search provider describing a
    page does not make that page safe to store or to fetch — the result set is
    third-party input, and a malicious entry in it is an ordinary thing to
    expect rather than a surprise.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text}"
    if not text.lower().startswith(("http://", "https://")):
        return None
    authority = text.split("://", 1)[1].split("/", 1)[0]
    if "@" in authority:
        return None
    text = text.split("#", 1)[0]
    try:
        validate_url(text)
    except (SSRFError, Exception):
        return None
    return text


async def search_target(
    session: Session,
    *,
    case_id: uuid.UUID,
    target_id: uuid.UUID,
    provider: SearchProvider | None = None,
    max_queries: int = MAX_QUERIES,
    limit: int = MAX_RESULTS_PER_QUERY,
    settings: Settings | None = None,
) -> IngestReport:
    """Run the recon plan through a configured provider and ingest what returns.

    With no provider configured this is a no-op that says so: the report must be
    able to distinguish "the public web returned nothing" from "the public web
    was never searched", and those are not the same finding.
    """
    settings = settings or get_settings()
    case = session.get(Case, case_id)
    target = session.get(Target, target_id)
    if case is None or target is None or target.case_id != case_id:
        raise ConfigurationError("The case or target does not exist")
    if target.type is not TargetType.PERSON:
        raise ConfigurationError("Provider search currently plans for PERSON targets only")

    engine = provider or get_search_provider(settings)
    available, reason = engine.is_available()
    report = IngestReport(
        provider=engine.key,
        configured=available,
        reason=reason,
        queries_as_planned=engine.runs_requested_query,
    )
    if not available:
        report.outcome = OUTCOME_UNAVAILABLE
        log.info("search_ingest.provider_not_configured", provider=engine.key)
        return report

    canonical = str(target.attributes.get("display_name", target.normalized_value))
    context = _context(target)
    variants = generate_variants(canonical)
    plan = staged_plan(canonical, context, variants=variants)
    queries = [item for stage in plan.stages for item in stage.queries][:max_queries]
    planned = planned_queries(queries)
    report.queries_planned = len(planned)

    brief = SearchBrief(subject=canonical, planned=planned, limit=limit)
    batch = await engine.discover(brief)
    _absorb_batch(report, batch, planned)

    store = EvidenceStore()
    seen: dict[str, Finding] = {}
    observed: dict[str, SearchResult] = {}
    moment = datetime.now(UTC)

    for result in batch.results:
        report.results_seen += 1
        outcome = _ingest_one(
            session,
            store=store,
            case_id=case_id,
            target=target,
            canonical=canonical,
            context=context,
            result=result,
            seen=seen,
            observed=observed,
            moment=moment,
            report=report,
        )
        if outcome == "stored":
            report.results_stored += 1
            if result.snippet is None:
                report.low_context_results += 1
        elif outcome == "duplicate":
            report.duplicates += 1
        else:
            report.rejected_urls += 1

    session.flush()
    await _enrich(
        session,
        canonical=canonical,
        context=context,
        seen=seen,
        observed=observed,
        report=report,
        settings=settings,
    )

    session.flush()
    report.findings = [finding.id for finding in seen.values()]
    log.info(
        "search_ingest.completed",
        provider=engine.key,
        planned=report.queries_planned,
        executed=report.queries_run,
        stored=report.results_stored,
        duplicates=report.duplicates,
        rejected=report.rejected_urls,
        outcome=report.outcome,
    )
    return report


def _absorb_batch(report: IngestReport, batch: SearchBatch, planned: tuple[Any, ...]) -> None:
    """Copy a provider's own account of what it did onto the ingest report.

    Deliberately the provider's numbers and not a recount of the result list. A
    search that returned nothing still ran, still cost money, and still has to
    appear in the execution record; counting result rows instead would erase it.
    """
    report.failures.extend(batch.failures)
    report.outcome = batch.outcome
    report.accounting = batch.accounting
    if batch.reason and not report.reason:
        report.reason = batch.reason
    report.executed_queries = list(dict.fromkeys(batch.executed_queries))
    report.queries_run = batch.accounting.searches_executed or len(batch.executed_queries)
    if report.queries_as_planned:
        families = {item.query: item.family for item in planned}
        report.image_queries_run = sum(
            1 for query in batch.executed_queries if families.get(query) == FAMILY_IMAGE
        )


async def _enrich(
    session: Session,
    *,
    canonical: str,
    context: PersonContext,
    seen: dict[str, Finding],
    observed: dict[str, SearchResult],
    report: IngestReport,
    settings: Settings,
) -> None:
    """Read a small number of the most promising description-less pages.

    Only pages whose provider returned no description, only the ones most likely
    to be about the subject, and never more than the configured budget. Every
    candidate considered is recorded with what was decided about it, so the report
    can distinguish a page that was read and said nothing from a page that was
    never read.
    """
    budget = EnrichmentBudget(limit=int(settings.max_result_enrichments_per_investigation))
    order = _enrichment_order(observed, canonical)
    for key in order:
        # Deliberately no early break. Once the budget is spent the remaining
        # candidates still go through, and each is recorded as "considered, not
        # read, because the budget was spent". Breaking here would leave the
        # report unable to distinguish a page nobody looked at from a page that
        # was looked at and said nothing — the distinction the whole coverage
        # vocabulary exists for. No request is made for a refused candidate.
        result = observed[key]
        finding = seen.get(key)
        if finding is None:
            continue
        classified = classify_url(key)
        excerpt = await fetch_excerpt(
            key,
            budget=budget,
            server_fetchable=classified.server_fetchable if classified else True,
            fetch_note=classified.fetch_note if classified else "",
            settings=settings,
        )
        _apply_excerpt(finding, result, excerpt, canonical, context, classified)
    report.enrichment = {
        **budget.to_dict(),
        "eligible_candidates": len(order),
        "policy": (
            "Only results whose provider returned no description are eligible, "
            "ranked by how likely the page is to be about the subject. Fetched "
            "through the platform's SSRF-guarded client, one request per page, "
            "robots.txt honoured, capped at the configured budget."
        ),
    }
    if order and budget.used == 0 and not settings.result_enrichment_enabled:
        report.enrichment["note"] = "Result enrichment is switched off for this deployment."


def _enrichment_order(observed: dict[str, SearchResult], canonical: str) -> list[str]:
    """Which description-less pages are worth reading, best first.

    "High value" is defined on what is already known about the URL and the title,
    because that is all there is before the fetch. A profile-shaped or
    publication-shaped URL is structured data about a person; a title carrying the
    subject's name is a page that at least claims to be about someone of that
    name. Everything else waits, and most of it is never read at all.
    """
    scored: list[tuple[int, int, str]] = []
    folded_name = _fold(canonical)
    parts = [part for part in folded_name.split() if len(part) > 2]
    for index, (key, result) in enumerate(observed.items()):
        if result.snippet is not None:
            # The provider described this page. Reading it ourselves would spend a
            # request to learn something we were already told.
            continue
        if result.host in NOISE_HOSTS:
            continue
        classified = classify_url(key)
        score = 0
        if classified is not None and classified.server_fetchable:
            score += 3 if classified.is_social else 2
            if classified.kind == "publication":
                score += 1
        title = _fold(result.title)
        if folded_name and folded_name in title:
            score += 3
        elif parts and all(part in title for part in parts):
            score += 2
        elif parts and any(part in title for part in parts):
            score += 1
        if score <= 0:
            continue
        scored.append((-score, index, key))
    return [key for _, _, key in sorted(scored)]


def _apply_excerpt(
    finding: Finding,
    result: SearchResult,
    excerpt: PageExcerpt,
    canonical: str,
    context: PersonContext,
    classified: SocialProfile | None,
) -> None:
    """Record one enrichment attempt on the finding, and rescore if it read text.

    The attempt is always recorded, including a refusal: "we did not read this
    page, and here is why" is information an investigator needs, and silence would
    leave the report unable to tell it from "we read it and it said nothing".
    """
    data = dict(finding.data or {})
    data["page_excerpt"] = excerpt.to_dict()
    data["enriched"] = excerpt.has_text
    finding.data = data
    if not excerpt.has_text:
        return

    candidate = _candidate_for(
        result,
        str(data.get("url") or result.url),
        canonical,
        context,
        classified,
        _strings(data.get("affiliations")),
        _strings(data.get("locations")),
        extra_text=excerpt.excerpt,
    )
    correlation = _correlate(candidate, canonical, context, enriched=True)
    _write_correlation(finding, correlation)


def _ingest_one(
    session: Session,
    *,
    store: EvidenceStore,
    case_id: uuid.UUID,
    target: Target,
    canonical: str,
    context: PersonContext,
    result: SearchResult,
    seen: dict[str, Finding],
    observed: dict[str, SearchResult],
    moment: datetime,
    report: IngestReport,
) -> str:
    """Store one result. Returns ``stored`` | ``duplicate`` | ``rejected``."""
    url = public_url(result.url)
    if url is None or (result.host in NOISE_HOSTS):
        return "rejected"

    classified = classify_url(url)
    key = classified.url if classified else url
    observed.setdefault(key, result)

    existing = seen.get(key) or session.scalar(
        select(Finding).where(
            Finding.case_id == case_id,
            Finding.dedupe_key == _dedupe_key(target, key),
        )
    )
    if existing is not None:
        # One page, many searches. The extra search is recorded as provenance, and
        # it adds no agreement: a provider returning the same URL for a second
        # query is the same source repeating itself.
        #
        # The *correlation* is nevertheless recomputed, because it is a pure
        # function of the page's observed content and the anchors currently on the
        # target — and those can change between runs. Without this, supplying an
        # employer and re-running left a page scored as though the anchor had
        # never been given, which is the stale-correlation defect PR #9 fixed on
        # the profile side arriving here by another route.
        _record_extra_search(existing, result)
        _refresh_correlation(existing, result, canonical, context, classified)
        seen[key] = existing
        session.flush()
        return "duplicate"

    candidate = _candidate_for(result, key, canonical, context, classified)
    correlation = _correlate(candidate, canonical, context)

    kind = _finding_kind(classified, result)
    data: dict[str, Any] = {
        "url": key,
        "host": (classified.platform if classified else result.host),
        "title": result.title,
        # None, not "": this provider published no description, and a reader of
        # the report is entitled to know which of those two it was.
        "snippet": result.snippet,
        "snippet_available": result.snippet is not None,
        "page_age": result.page_age or None,
        "candidate_name": candidate.name,
        "candidate_key": key,
        "subject_name": canonical,
        "subject_value": target.normalized_value,
        "canonical_target": canonical,
        "source": SEARCH_COLLECTOR,
        "source_label": SEARCH_SOURCE_LABEL,
        "evidence_class": EVIDENCE_CLASS,
        # Which channel served the page, and which public source the claim is
        # about. Kept apart on purpose: the channel is never the origin.
        "retrieval_channel": result.provider,
        "claim_origin": key,
        "claim_origin_domain": result.host,
        "name_variant_type": correlation.variant_type,
        "name_variant_reason": correlation.variant_reason,
        "match_reasons": correlation.match_reasons,
        "mismatch_reasons": correlation.mismatch_reasons,
        "corroborated_by": correlation.corroborated_by,
        # Anchors the page positively disagrees with. Never subtracted from the
        # score — a conflict is for a human to rule the candidate out with.
        "conflicts": correlation.conflicts,
        "identifiers": {},
        "affiliations": list(candidate.affiliations),
        "locations": list(candidate.locations),
        "handles": candidate.handles,
        # Every search that surfaced this page, so a report can show how it was
        # found without implying each one was a separate source.
        "searches": [_search_record(result)],
        "search_provider": result.provider,
    }
    if classified:
        data.update(
            {
                "platform": classified.platform,
                "platform_label": classified.display_name,
                "url_kind": classified.kind,
                "handle": classified.handle,
                "server_fetchable": classified.server_fetchable,
                "fetch_note": classified.fetch_note,
            }
        )
    image_url = public_url(result.image_url) if result.image_url else None
    if image_url:
        data.update(
            {
                "image_url": image_url,
                "source_page_url": key,
                "caption": result.title or None,
                "analysis": "none",
                "biometric_matching": False,
                "interpretation": (
                    "Context evidence only: the search provider returned this image "
                    "alongside the page. The platform performs no facial recognition "
                    "and makes no claim about who is depicted."
                ),
            }
        )

    finding = Finding(
        case_id=case_id,
        target_id=target.id,
        kind=kind,
        title=result.title or key,
        summary=_summary(result)[:1000],
        data=data,
        collector=SEARCH_COLLECTOR,
        source_url=key,
        confidence=correlation.score,
        confidence_reasons=[*correlation.match_reasons, *correlation.mismatch_reasons],
        classification=Classification.PERSONAL,
        observed_at=moment,
        dedupe_key=_dedupe_key(target, key),
    )
    session.add(finding)
    session.flush()

    store.store(
        session,
        case_id=case_id,
        collector=SEARCH_COLLECTOR,
        source_url=key,
        content={
            "result": result.to_dict(),
            "normalized_url": key,
            "evidence_class": EVIDENCE_CLASS,
            "retrieved_at": moment.isoformat(),
        },
        retrieved_at=moment,
        finding=finding,
    )
    seen[key] = finding
    report.new_findings.append(finding.id)
    return "stored"


def _summary(result: SearchResult) -> str:
    """One sentence about the page, stating only what is known.

    A provider that returned no description gets a sentence that says exactly
    that. Filling the gap with the title, the URL or a generated description would
    put a claim in the record that no public source made.
    """
    if result.snippet:
        return result.snippet
    executed = result.query or result.planned_query
    found = f"Discovered through {result.provider}"
    if executed:
        found += f" by the search {executed!r}"
    if result.snippet is None:
        return (
            f"{found}. This provider returns no page description, so the page is "
            f"recorded as a discovery candidate and correlated on its displayed "
            f"name until its own text is read."
        )
    return f"{found}. The provider returned an empty description for it."


@dataclass(slots=True)
class _Correlation:
    """One scoring of one page against the target. Replaceable, never merged."""

    score: float
    variant_type: str
    variant_reason: str
    match_reasons: list[str]
    mismatch_reasons: list[str]
    corroborated_by: list[str]
    conflicts: list[str]
    affiliations: list[str]
    locations: list[str]


def _candidate_for(
    result: SearchResult,
    key: str,
    canonical: str,
    context: PersonContext,
    classified: Any,
    extra_affiliations: Sequence[str] = (),
    extra_locations: Sequence[str] = (),
    *,
    extra_text: str = "",
) -> PersonCandidate:
    """The candidate a result describes.

    The name is what the result *displays*, not what was searched for: using the
    canonical name here would make every hit an exact name match, which is the
    opposite of what variant-aware scoring is for.

    What a result can be seen to *say* about affiliations and places is limited
    to the anchors it is checked against, and to the text actually available: a
    title, a provider description where there is one, and a fetched public excerpt
    where enrichment read the page. It can confirm that a supplied employer
    appears in that text; it cannot extract an employer nobody supplied. The
    ``extra_*`` arguments carry forward what earlier text about the same page was
    seen to name, which is how a page can still be found to disagree with an
    anchor the investigator has since changed.
    """
    text = _result_text(result, extra_text)
    return PersonCandidate(
        url=key,
        name=(result.title or "").strip() or result.search_variant or canonical,
        summary=(result.snippet_text or extra_text)[:500],
        handles=[classified.handle] if classified and classified.handle else [],
        affiliations=list(
            dict.fromkeys([*_values_in(text, context.affiliations), *extra_affiliations])
        ),
        locations=list(dict.fromkeys([*_values_in(text, context.places), *extra_locations])),
    )


def _correlate(
    candidate: PersonCandidate,
    canonical: str,
    context: PersonContext,
    *,
    enriched: bool = False,
) -> _Correlation:
    """Score a page against the subject, through the shared assessment.

    Deliberately :func:`app.collectors.person.assess` rather than a local signal
    set. A second implementation is how two paths come to disagree about the same
    evidence, and it had already cost this codebase that bug twice — once on the
    promoted profile, once on the provider finding. Using the shared one also
    means a result inherits conflict detection for free: a page that states an
    employer contradicting a supplied one now says so.
    """
    assessment = assess(candidate, canonical, context)
    mismatch = [
        "A public search returned this page; that a search engine ranked it says "
        "nothing about whether it is about the subject",
        *assessment.mismatch_reasons,
    ]
    match = list(assessment.match_reasons)
    if candidate.affiliations:
        source = "the page's own text, fetched directly" if enriched else "the page's own text"
        match.insert(
            0,
            f"{', '.join(candidate.affiliations)} appears in {source}, and you supplied "
            f"it as an affiliation — the anchor and the page agree independently",
        )
    if not candidate.affiliations and not candidate.locations and not enriched:
        mismatch.append(
            "No supplied anchor was observed in this page's available text, so the "
            "only thing linking it to the subject is the name it displays"
        )
    return _Correlation(
        score=default_engine.score(assessment.signals).score,
        variant_type=assessment.name_variant_type,
        variant_reason=assessment.name_variant_reason,
        match_reasons=match,
        mismatch_reasons=mismatch,
        corroborated_by=list(assessment.corroborated_by),
        conflicts=list(assessment.conflicts),
        affiliations=list(candidate.affiliations),
        locations=list(candidate.locations),
    )


def _refresh_correlation(
    finding: Finding,
    result: SearchResult,
    canonical: str,
    context: PersonContext,
    classified: Any,
) -> None:
    """Re-derive a stored page's correlation from the current anchors.

    Observed *content* accumulates — every text was really seen, so an affiliation
    read from an earlier description is not discarded by a later result that
    omitted it, and neither is one read from a fetched excerpt. The *correlation*
    is replaced outright. Both halves match what ``record_profile`` does, so the
    finding and its promoted profile cannot drift apart again.
    """
    data = dict(finding.data or {})
    excerpt = data.get("page_excerpt")
    extra_text = ""
    if isinstance(excerpt, dict) and isinstance(excerpt.get("excerpt"), str):
        extra_text = excerpt["excerpt"]
    candidate = _candidate_for(
        result,
        str(data.get("url") or ""),
        canonical,
        context,
        classified,
        _strings(data.get("affiliations")),
        _strings(data.get("locations")),
        extra_text=extra_text,
    )
    correlation = _correlate(candidate, canonical, context, enriched=bool(extra_text))
    _write_correlation(finding, correlation)


def _write_correlation(finding: Finding, correlation: _Correlation) -> None:
    """Replace a finding's correlation with a freshly computed one."""
    data = dict(finding.data or {})
    data.update(
        {
            "name_variant_type": correlation.variant_type,
            "name_variant_reason": correlation.variant_reason,
            "match_reasons": correlation.match_reasons,
            "mismatch_reasons": correlation.mismatch_reasons,
            "corroborated_by": correlation.corroborated_by,
            "conflicts": correlation.conflicts,
            "affiliations": correlation.affiliations,
            "locations": correlation.locations,
        }
    )
    finding.data = data
    finding.confidence = correlation.score
    finding.confidence_reasons = [*correlation.match_reasons, *correlation.mismatch_reasons]


def _strings(value: Any) -> list[str]:
    """The string members of a stored JSON list, and nothing else."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _result_text(result: SearchResult, extra_text: str = "") -> str:
    """The public text available about a result.

    Its title, whatever description the provider supplied, its domain, and — only
    where enrichment actually read the page — an excerpt of the page's own visible
    text. Nothing synthesised, and nothing from the provider that the provider did
    not publish.
    """
    return " ".join(
        part
        for part in (
            result.title,
            result.snippet_text,
            result.displayed_url,
            result.host,
            extra_text,
        )
        if part
    )


def _values_in(text: str, values: Sequence[str]) -> list[str]:
    """Supplied values that appear in a result's own public text.

    Deliberately the *supplied* value found in the text, not a value extracted
    from it. Extracting one would mean guessing which words name an employer or a
    place; observing that the employer the investigator already named appears in a
    public page about this name is a different and much stronger thing. That
    co-occurrence is what the existing ``context_affiliation_match`` and
    ``context_location_match`` rules mean, so it fires those rules and no other.
    Nothing here infers a nationality, a residence or a current location from a
    place appearing on a page.
    """
    haystack = _fold(text)
    return [value for value in values if value.strip() and _fold(value) in haystack]


def _fold(text: str) -> str:
    return " ".join((text or "").lower().split())


def _finding_kind(classified: Any, result: SearchResult) -> FindingKind:
    """What kind of evidence this result is.

    A profile-shaped URL is a person candidate; a PDF is a document; anything
    else is a web page that mentions the name. Decided from the URL's own shape
    rather than from what the provider called it, because the provider is
    describing its index and the URL is the thing itself.
    """
    if result.image_url:
        return FindingKind.IMAGE_EVIDENCE
    if classified is not None and classified.is_social:
        return FindingKind.PERSON_CANDIDATE
    lowered = result.url.lower().split("?", 1)[0]
    if lowered.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")):
        return FindingKind.PUBLIC_DOCUMENT
    if classified is not None and classified.kind == "publication":
        return FindingKind.PUBLIC_DOCUMENT
    return FindingKind.SEARCH_RESULT


def _dedupe_key(target: Target, url: str) -> str:
    return f"provider-search:{target.normalized_value}:{url}"


def _search_record(result: SearchResult) -> dict[str, Any]:
    """One search that found this page, as it really happened.

    ``planned_query`` and ``executed_query`` are both recorded, and so is whether
    they are the same. On a provider that chooses its own queries the planned
    field is empty, because attributing the result to a query the platform merely
    hoped for would be a fabrication — and the whole point of storing both is that
    a reader can tell.
    """
    return {
        "planned_query": result.planned_query or None,
        "executed_query": result.query or None,
        "query_executed_as_planned": result.query_executed_as_planned,
        "search_variant": result.search_variant,
        "variant_type": result.variant_type,
        "query_family": result.query_family,
        "provider": result.provider,
        "retrieval_channel": result.provider,
        "provider_position": result.provider_position,
        "position_is_rank": result.position_is_rank,
        "page_age": result.page_age or None,
        "server_tool_use_id": result.retrieval_call_id or None,
        "tool_result_id": result.retrieval_result_id or None,
        "retrieved_at": result.retrieved_at.isoformat() if result.retrieved_at else None,
    }


def _record_extra_search(finding: Finding, result: SearchResult) -> None:
    """Append a search that found an already-known page, without rescoring it.

    This is the no-double-counting rule made concrete. The page is one source;
    how many queries reached it is provenance about the search, not additional
    agreement about the subject, so ``confidence`` is deliberately untouched.
    """
    data = dict(finding.data or {})
    searches = list(data.get("searches") or [])
    record = _search_record(result)
    if not any(
        item.get("executed_query") == record["executed_query"]
        and item.get("search_variant") == record["search_variant"]
        for item in searches
    ):
        searches.append(record)
    data["searches"] = searches
    finding.data = data


def _context(target: Target) -> PersonContext:
    return PersonContext.from_target(
        NormalizedTarget(
            type=target.type,
            raw_input=target.raw_input,
            value=target.normalized_value,
            attributes=dict(target.attributes or {}),
        )
    )
