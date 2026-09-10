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

Three rules shape it.

**A search result is discovery evidence, not identity proof.** Nothing gets a
confidence boost for having been returned by a search engine. The name rule that
fires depends on which *spelling* found it, so a reduced-name hit stays weak
until an anchor corroborates it.

**The same page found four ways is still one page.** A URL reached from two
queries and two name variants is one source with four provenance records, not
four agreeing sources. Corroboration counts sources, and a search engine
repeating itself is not a second source.

**Provider output is untrusted input.** Every URL is validated by the existing
SSRF guard before it is stored or fetched, and nothing here opens a second HTTP
path — the provider call and any image fetch go through ``app.core.http``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.person import PersonCandidate, PersonContext, anchor_matches
from app.collectors.social import classify_url
from app.core.errors import ConfigurationError, SSRFError
from app.core.logging import get_logger
from app.core.ssrf import validate_url
from app.correlation.anchors import ANCHOR_REASONS, ANCHOR_RULES
from app.correlation.confidence import default_engine
from app.models import Case, Finding, Target
from app.models.enums import Classification, FindingKind, TargetType
from app.services.evidence import EvidenceStore
from app.services.name_variants import (
    NameVariant,
    classify_observed_name,
    confidence_rule_for,
    generate_variants,
)
from app.services.normalization import NormalizedTarget
from app.services.providers.search import SearchProvider, SearchResult, get_search_provider
from app.services.recon import ReconQuery, staged_plan

log = get_logger(__name__)

#: The collector identity automated search results are filed under. Distinct
#: from ``search`` (the older single-query collector) and from
#: ``manual_search_recon``, so a report can tell the three apart at a glance.
SEARCH_COLLECTOR = "provider_search"
SEARCH_SOURCE_LABEL = "Public web search"
EVIDENCE_CLASS = "provider_search_result"

#: Queries sent per ingestion run, and results taken from each. Bounded because
#: a provider costs money per call and an investigator reads a worklist, not a
#: corpus.
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


@dataclass(slots=True)
class IngestReport:
    """What one ingestion run did, for the API and the report's coverage table."""

    provider: str
    configured: bool
    reason: str = ""
    queries_run: int = 0
    results_seen: int = 0
    results_stored: int = 0
    duplicates: int = 0
    rejected_urls: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    findings: list[uuid.UUID] = field(default_factory=list)

    @property
    def searched(self) -> bool:
        return self.queries_run > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "reason": self.reason or None,
            "queries_run": self.queries_run,
            "results_seen": self.results_seen,
            "results_stored": self.results_stored,
            "duplicates": self.duplicates,
            "rejected_urls": self.rejected_urls,
            "failures": list(self.failures),
            "findings": [str(item) for item in self.findings],
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
) -> IngestReport:
    """Run the recon plan through a configured provider and ingest what returns.

    With no provider configured this is a no-op that says so: the report must be
    able to distinguish "the public web returned nothing" from "the public web
    was never searched", and those are not the same finding.
    """
    case = session.get(Case, case_id)
    target = session.get(Target, target_id)
    if case is None or target is None or target.case_id != case_id:
        raise ConfigurationError("The case or target does not exist")
    if target.type is not TargetType.PERSON:
        raise ConfigurationError("Provider search currently plans for PERSON targets only")

    engine = provider or get_search_provider()
    available, reason = engine.is_available()
    report = IngestReport(provider=engine.key, configured=available, reason=reason)
    if not available:
        log.info("search_ingest.provider_not_configured", provider=engine.key)
        return report

    canonical = str(target.attributes.get("display_name", target.normalized_value))
    context = _context(target)
    variants = generate_variants(canonical)
    plan = staged_plan(canonical, context, variants=variants)
    queries = [item for stage in plan.stages for item in stage.queries][:max_queries]

    store = EvidenceStore()
    seen: dict[str, Finding] = {}
    moment = datetime.now(UTC)

    for query in queries:
        variant = _variant_for_query(query, variants)
        try:
            results = await engine.search(query.query, limit=limit)
        except Exception as exc:
            report.failures.append(
                {
                    "query": query.query,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )
            log.warning(
                "search_ingest.query_failed",
                provider=engine.key,
                error_type=type(exc).__name__,
            )
            continue

        report.queries_run += 1
        for result in results:
            report.results_seen += 1
            enriched = result.with_provenance(
                query=query.query,
                search_variant=variant.value,
                variant_type=variant.variant_type,
                query_family=query.family,
                retrieved_at=moment,
            )
            outcome = _ingest_one(
                session,
                store=store,
                case_id=case_id,
                target=target,
                canonical=canonical,
                context=context,
                result=enriched,
                seen=seen,
                moment=moment,
            )
            if outcome == "stored":
                report.results_stored += 1
            elif outcome == "duplicate":
                report.duplicates += 1
            else:
                report.rejected_urls += 1

    session.flush()
    report.findings = [finding.id for finding in seen.values()]
    log.info(
        "search_ingest.completed",
        provider=engine.key,
        queries=report.queries_run,
        stored=report.results_stored,
        duplicates=report.duplicates,
        rejected=report.rejected_urls,
    )
    return report


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
    moment: datetime,
) -> str:
    """Store one result. Returns ``stored`` | ``duplicate`` | ``rejected``."""
    url = public_url(result.url)
    if url is None or (result.host in NOISE_HOSTS):
        return "rejected"

    classified = classify_url(url)
    key = classified.url if classified else url

    existing = seen.get(key) or session.scalar(
        select(Finding).where(
            Finding.case_id == case_id,
            Finding.dedupe_key == _dedupe_key(target, key),
        )
    )
    if existing is not None:
        # One page, many searches. The extra search is recorded as provenance and
        # changes no score: a provider returning the same URL for a second query
        # is the same source agreeing with itself.
        _record_extra_search(existing, result)
        seen[key] = existing
        session.flush()
        return "duplicate"

    displayed_name = (result.title or "").strip()
    candidate = PersonCandidate(
        url=key,
        # What the result *displays*, not what we searched for. Using the
        # canonical name here would make every hit an exact name match.
        name=displayed_name or result.search_variant or canonical,
        summary=(result.snippet or "")[:500],
        handles=[classified.handle] if classified and classified.handle else [],
        affiliations=_anchors_in_text(result, context),
        locations=_places_in_text(result, context),
    )
    variant_type, variant_reason = classify_observed_name(candidate.name, canonical)
    matched = anchor_matches(candidate, context)

    signals = [default_engine.signal(confidence_rule_for(variant_type))]
    match_reasons: list[str] = []
    if candidate.affiliations:
        match_reasons.append(
            f"The page's own text names {', '.join(candidate.affiliations)}, which you "
            f"supplied as an affiliation — the anchor and the page agree independently"
        )
    corroborated: list[str] = []
    for kind, detail in matched:
        signals.append(default_engine.signal(ANCHOR_RULES[kind], detail=detail))
        corroborated.append(kind)
        match_reasons.append(ANCHOR_REASONS[kind].format(detail=detail))
    score = default_engine.score(signals)

    mismatch_reasons = [
        "A public search returned this page; that a search engine ranked it says "
        "nothing about whether it is about the subject",
    ]
    if variant_reason:
        mismatch_reasons.append(variant_reason)
    if not corroborated:
        mismatch_reasons.append(
            "Nothing beyond the name connects this page to the subject"
            + ("" if context.is_empty() else " — none of the anchors you supplied appears in it")
        )

    kind = _finding_kind(classified, result)
    data: dict[str, Any] = {
        "url": key,
        "host": (classified.platform if classified else result.host),
        "title": result.title,
        "snippet": result.snippet,
        "candidate_name": candidate.name,
        "candidate_key": key,
        "subject_name": canonical,
        "subject_value": target.normalized_value,
        "canonical_target": canonical,
        "source": SEARCH_COLLECTOR,
        "source_label": SEARCH_SOURCE_LABEL,
        "evidence_class": EVIDENCE_CLASS,
        "name_variant_type": variant_type,
        "name_variant_reason": variant_reason,
        "match_reasons": match_reasons,
        "mismatch_reasons": mismatch_reasons,
        "corroborated_by": corroborated,
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
        summary=(result.snippet or f"Public page returned for {result.query!r}.")[:1000],
        data=data,
        collector=SEARCH_COLLECTOR,
        source_url=key,
        confidence=score.score,
        confidence_reasons=[*score.reasons, *mismatch_reasons],
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
    return "stored"


def _result_text(result: SearchResult) -> str:
    """The public text a result shows: its title, its snippet, its domain."""
    return " ".join(
        part for part in (result.title, result.snippet, result.displayed_url, result.host) if part
    )


def _anchors_in_text(result: SearchResult, context: PersonContext) -> list[str]:
    """Supplied affiliations that appear in the result's own public text.

    Deliberately the *supplied* value found in the text, not an organisation
    extracted from it. Extracting one would mean guessing which words in a
    snippet name an employer; observing that the employer the investigator
    already named appears in a public page about this name is a different and
    much stronger thing. That co-occurrence is what the existing
    ``context_affiliation_match`` rule means, so it fires that rule and no other.
    """
    haystack = _fold(_result_text(result))
    return [value for value in context.affiliations if value.strip() and _fold(value) in haystack]


def _places_in_text(result: SearchResult, context: PersonContext) -> list[str]:
    """Supplied places that appear in the result's own public text.

    Coarse by construction — the anchors are a city or a country — and it
    narrows a result list rather than locating anybody. Nothing here infers a
    nationality, a residence or a current location from a place appearing on a
    page.
    """
    haystack = _fold(_result_text(result))
    return [value for value in context.places if value.strip() and _fold(value) in haystack]


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
    return {
        "query": result.query,
        "search_variant": result.search_variant,
        "variant_type": result.variant_type,
        "query_family": result.query_family,
        "provider": result.provider,
        "rank": result.rank,
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
        item.get("query") == record["query"]
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


def _variant_for_query(query: ReconQuery, variants: list[NameVariant]) -> NameVariant:
    """The variant a query was built from, falling back to the exact spelling."""
    for variant in variants:
        if variant.value == query.name_variant:
            return variant
    return variants[0]
