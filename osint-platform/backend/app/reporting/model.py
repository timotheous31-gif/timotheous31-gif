"""The report model.

Reports are assembled in two steps: this module gathers a case into a plain
data structure, and the renderers turn that structure into HTML, Markdown or
JSON. Splitting them means every renderer shows the same content and the
gathering logic — including the export privacy policy — is tested once.

The rule the model exists to enforce: **every claim carries its evidence**.
Findings, entities and relationships all reference the evidence records that
support them, and the renderers surface those references rather than presenting
bare assertions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.ssrf import is_safe_url
from app.correlation.confidence import classify
from app.graph import build_graph, graph_summary
from app.models import (
    AnalystDecisionRecord,
    Case,
    CollectorRun,
    Entity,
    Evidence,
    Finding,
    ImageEvidence,
    Job,
    Relationship,
    SocialProfile,
    Target,
    TimelineEvent,
)
from app.models.enums import Classification, RunStatus, TargetType
from app.privacy.filter import PrivacyFilter
from app.reporting.coverage import (
    CoverageItem,
    CoverageState,
    image_search_coverage,
    manual_only_platforms,
    state_for_run,
    summarise_gaps,
    web_search_coverage,
)
from app.services.timeline import timeline_summary

log = get_logger(__name__)

#: How many findings the "key findings" section shows.
KEY_FINDING_LIMIT = 25


@dataclass(slots=True)
class EvidenceRef:
    """A citation an investigator can follow and re-verify."""

    id: str
    sha256: str
    source_url: str | None
    collector: str
    retrieved_at: datetime
    excerpt: str | None
    redacted: bool

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sha256": self.sha256,
            "source_url": self.source_url,
            "collector": self.collector,
            "retrieved_at": self.retrieved_at.isoformat(),
            "excerpt": self.excerpt,
            "redacted": self.redacted,
        }


@dataclass(slots=True)
class FindingItem:
    id: str
    kind: str
    title: str
    summary: str | None
    data: dict[str, Any]
    collector: str
    source_url: str | None
    confidence: float
    confidence_reasons: list[str]
    classification: str
    redacted: bool
    withheld: bool
    observed_at: datetime | None
    evidence: list[EvidenceRef] = field(default_factory=list)

    @property
    def strength(self) -> str:
        return str(classify(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "data": self.data,
            "collector": self.collector,
            "source_url": self.source_url,
            "confidence": round(self.confidence, 3),
            "confidence_strength": self.strength,
            "confidence_reasons": self.confidence_reasons,
            "classification": self.classification,
            "redacted": self.redacted,
            "withheld": self.withheld,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(slots=True)
class EntityItem:
    id: str
    type: str
    display_name: str
    canonical_value: str
    aliases: list[str]
    attributes: dict[str, Any]
    confidence: float
    confidence_reasons: list[str]
    source_finding_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "display_name": self.display_name,
            "canonical_value": self.canonical_value,
            "aliases": self.aliases,
            "attributes": self.attributes,
            "confidence": round(self.confidence, 3),
            "confidence_reasons": self.confidence_reasons,
            "source_finding_ids": self.source_finding_ids,
        }


@dataclass(slots=True)
class RelationshipItem:
    id: str
    type: str
    source_label: str
    target_label: str
    source_id: str
    target_id: str
    confidence: float
    strength: str
    confidence_reasons: list[str]
    collector: str
    evidence_finding_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": {"id": self.source_id, "label": self.source_label},
            "target": {"id": self.target_id, "label": self.target_label},
            "confidence": round(self.confidence, 3),
            "strength": self.strength,
            "confidence_reasons": self.confidence_reasons,
            "collector": self.collector,
            "evidence_finding_ids": self.evidence_finding_ids,
        }


@dataclass(slots=True)
class SourceItem:
    """One collector that contributed, and how it performed."""

    collector: str
    version: str
    runs: int
    successes: int
    failures: int
    skipped: int
    findings: int
    attribution: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "version": self.version,
            "runs": self.runs,
            "successes": self.successes,
            "failures": self.failures,
            "skipped": self.skipped,
            "findings": self.findings,
            "attribution": self.attribution,
        }


@dataclass(slots=True)
class SocialProfileItem:
    """A public profile page as a report renders it."""

    id: str
    platform: str
    platform_label: str
    handle: str | None
    profile_url: str
    display_name: str | None
    accessibility: str
    server_fetchable: bool
    fetch_note: str | None
    collector: str
    evidence_class: str
    #: Computed by the platform. Never edited by an analyst decision.
    confidence: float
    match_reasons: list[str]
    mismatch_reasons: list[str]
    corroborated_by: list[str]
    candidate_id: str | None
    retrieved_at: datetime | None
    #: How the profile entered the case, from a closed vocabulary, and the
    #: public page that published the link when one did.
    discovery_method: str | None = None
    discovered_from: str | None = None
    #: What the account states about itself on its own profile page, each entry
    #: carrying the line it was read from. Explicit statements only.
    profile_facts: list[dict[str, Any]] = field(default_factory=list)
    #: The name the source declares, beside the name that was searched, and how
    #: the two relate. Recorded, never applied: the target keeps its own name.
    declared_name: str | None = None
    searched_name: str | None = None
    name_relationship: dict[str, str] | None = None
    #: The page the statements were read from.
    detail_source_url: str | None = None
    detail_note: str | None = None
    #: The analyst's separate judgement, shown alongside rather than instead.
    analyst_decision: str | None = None
    analyst_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "platform_label": self.platform_label,
            "handle": self.handle,
            "profile_url": self.profile_url,
            "display_name": self.display_name,
            "accessibility": self.accessibility,
            "server_fetchable": self.server_fetchable,
            "fetch_note": self.fetch_note,
            "collector": self.collector,
            "evidence_class": self.evidence_class,
            "confidence": round(self.confidence, 4),
            "match_reasons": self.match_reasons,
            "mismatch_reasons": self.mismatch_reasons,
            "corroborated_by": self.corroborated_by,
            "candidate_id": self.candidate_id,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "discovery_method": self.discovery_method,
            "discovered_from": self.discovered_from,
            "profile_facts": self.profile_facts,
            "declared_name": self.declared_name,
            "searched_name": self.searched_name,
            "name_relationship": self.name_relationship,
            "detail_source_url": self.detail_source_url,
            "detail_note": self.detail_note,
            "analyst_decision": self.analyst_decision,
            "analyst_note": self.analyst_note,
        }


#: Said on every image, in every format. It is the one thing a reader might
#: otherwise assume, and assuming it is how a photograph becomes an accusation.
IMAGE_DISCLAIMER = (
    "This image appears on a public page associated with this candidate. It does not "
    "independently establish identity."
)


def render_safety(image_url: str) -> tuple[bool, str]:
    """Whether a viewer may be pointed at this URL to draw it inline.

    Shape only — scheme, credentials, literal address — with no DNS lookup:
    building a report must not perform network resolution per image, and a name
    that resolved safely at fetch time can resolve elsewhere by the time a
    reader opens the document. The resolving check runs where it matters, in
    the SSRF-guarded fetch path.

    ``http`` is refused as well as unsafe hosts: a plaintext image request from
    inside a report discloses to the network what is being read.
    """
    url = (image_url or "").strip()
    if not url.lower().startswith("https://"):
        return False, "Not drawn inline: only https image URLs are rendered."
    if not is_safe_url(url, resolve=False):
        return False, "Not drawn inline: the URL is not a safe public address."
    return True, ""


@dataclass(slots=True)
class ImageItem:
    """A public image as page context, with its provenance.

    Never a biometric claim: ``analysis`` and ``biometric_matching`` are carried
    into the report so the limit travels with the data rather than living only
    in the code that produced it.

    ``render_safe`` says whether a viewer may be pointed at ``image_url`` to
    draw it. Whether a given format *does* draw it is the renderer's decision —
    an interactive view can, a document that will be opened later somewhere
    else should not fetch third-party URLs on the reader's behalf.
    """

    id: str
    image_url: str
    source_page_url: str
    platform: str | None
    caption: str | None
    fetch_state: str
    #: Present only when the bytes were actually read.
    sha256: str | None
    content_type: str | None
    byte_length: int | None
    width: int | None
    height: int | None
    redirects: list[str]
    final_url: str | None
    fetch_note: str | None
    origin: str
    evidence_class: str
    candidate_id: str | None
    retrieved_at: datetime | None
    #: Who and what the picture is filed against, so a report reads as evidence
    #: about a candidate rather than as a bare URL.
    platform_label: str | None = None
    profile_url: str | None = None
    handle: str | None = None
    candidate_name: str | None = None
    #: The candidate's automated confidence at render time. Never the image's:
    #: a picture carries no identity score of its own, because nothing here
    #: analyses it.
    candidate_confidence: float | None = None
    render_safe: bool = False
    render_note: str = ""
    disclaimer: str = IMAGE_DISCLAIMER
    analysis: str = "none"
    biometric_matching: bool = False
    analyst_decision: str | None = None
    analyst_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "image_url": self.image_url,
            "source_page_url": self.source_page_url,
            "platform": self.platform,
            "caption": self.caption,
            "fetch_state": self.fetch_state,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "byte_length": self.byte_length,
            "dimensions": {"width": self.width, "height": self.height},
            "redirects": self.redirects,
            "final_url": self.final_url,
            "fetch_note": self.fetch_note,
            "origin": self.origin,
            "evidence_class": self.evidence_class,
            "candidate_id": self.candidate_id,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "platform_label": self.platform_label,
            "profile_url": self.profile_url,
            "handle": self.handle,
            "candidate_name": self.candidate_name,
            "candidate_confidence": (
                round(self.candidate_confidence, 4)
                if self.candidate_confidence is not None
                else None
            ),
            "render_safe": self.render_safe,
            "render_note": self.render_note or None,
            "disclaimer": self.disclaimer,
            "analysis": self.analysis,
            "biometric_matching": self.biometric_matching,
            "analyst_decision": self.analyst_decision,
            "analyst_note": self.analyst_note,
        }


@dataclass(slots=True)
class PublicContactItem:
    """A publicly published professional contact point, with its provenance."""

    id: str
    contact_type: str
    value: str
    label: str | None
    classification: str
    source_name: str
    source_url: str | None
    collector: str
    evidence_class: str
    #: Computed by the platform. Never edited by an analyst decision.
    confidence: float
    confidence_reasons: list[str]
    #: Why this value was promoted out of a raw payload.
    extraction_reason: str | None
    candidate_id: str | None
    retrieved_at: datetime | None
    analyst_decision: str | None = None
    analyst_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contact_type": self.contact_type,
            "value": self.value,
            "label": self.label,
            "classification": self.classification,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "collector": self.collector,
            "evidence_class": self.evidence_class,
            "confidence": round(self.confidence, 4),
            "confidence_reasons": self.confidence_reasons,
            "extraction_reason": self.extraction_reason,
            "candidate_id": self.candidate_id,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "analyst_decision": self.analyst_decision,
            "analyst_note": self.analyst_note,
        }


@dataclass(slots=True)
class AnalystDecisionItem:
    """One recorded human judgement."""

    subject_type: str
    subject_id: str
    decision: str
    note: str | None
    decided_by: str | None
    decided_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "decision": self.decision,
            "note": self.note,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
        }


@dataclass(slots=True)
class ExecutionSummary:
    """The latest investigation execution, told apart from the case's history.

    A case with five reruns had forty collector rows, and a report that simply
    counted them read as though the investigation had used forty sources. It had
    used eight, five times. No new entity was needed to say so: a ``Job`` *is* an
    execution, and every ``CollectorRun`` already records the ``job_id`` it
    belonged to — so the current execution is the latest job's runs, and the rest
    is history that stays exactly where it is.
    """

    #: How many distinct executions this case has had.
    executions: int = 0
    latest_job_id: str | None = None
    latest_started_at: datetime | None = None
    latest_finished_at: datetime | None = None
    latest_state: str | None = None
    #: The latest execution only.
    collectors_attempted: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    #: Findings attributable to the collectors this execution used. Not "findings
    #: this execution created": findings are deduplicated across reruns, so no
    #: such number exists, and inventing one would be worse than omitting it.
    findings: int = 0
    #: Every run the case has ever recorded. Kept, never conflated.
    historical_runs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "executions": self.executions,
            "latest": {
                "job_id": self.latest_job_id,
                "state": self.latest_state,
                "started_at": (
                    self.latest_started_at.isoformat() if self.latest_started_at else None
                ),
                "finished_at": (
                    self.latest_finished_at.isoformat() if self.latest_finished_at else None
                ),
                "collectors_attempted": self.collectors_attempted,
                "successful": self.successful,
                "failed": self.failed,
                "skipped": self.skipped,
                "findings": self.findings,
            },
            "historical_collector_runs": self.historical_runs,
        }


@dataclass(slots=True)
class ReportModel:
    """Everything a report renders."""

    case_id: str
    case_name: str
    case_description: str | None
    case_status: str
    case_notes: str | None
    case_tags: list[str]
    created_at: datetime
    generated_at: datetime
    platform_version: str

    targets: list[dict[str, Any]] = field(default_factory=list)
    executive_summary: list[str] = field(default_factory=list)
    key_findings: list[FindingItem] = field(default_factory=list)
    findings: list[FindingItem] = field(default_factory=list)
    entities: list[EntityItem] = field(default_factory=list)
    relationships: list[RelationshipItem] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    social_profiles: list[SocialProfileItem] = field(default_factory=list)
    images: list[ImageItem] = field(default_factory=list)
    public_contacts: list[PublicContactItem] = field(default_factory=list)
    analyst_decisions: list[AnalystDecisionItem] = field(default_factory=list)
    sources: list[SourceItem] = field(default_factory=list)
    #: What was searched, what was not, and why. The section that keeps "nothing
    #: was found" from being read as "there is nothing to find".
    coverage: list[CoverageItem] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    execution: ExecutionSummary = field(default_factory=lambda: ExecutionSummary())
    graph: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    methodology: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": {
                "id": self.case_id,
                "name": self.case_name,
                "description": self.case_description,
                "status": self.case_status,
                "notes": self.case_notes,
                "tags": self.case_tags,
                "created_at": self.created_at.isoformat(),
            },
            "generated_at": self.generated_at.isoformat(),
            "platform_version": self.platform_version,
            "policy": self.policy,
            "counts": self.counts,
            "executive_summary": self.executive_summary,
            "targets": self.targets,
            "key_findings": [item.to_dict() for item in self.key_findings],
            "findings": [item.to_dict() for item in self.findings],
            "entities": [item.to_dict() for item in self.entities],
            "relationships": [item.to_dict() for item in self.relationships],
            "confidence": self.confidence,
            "timeline": self.timeline,
            "evidence": [ref.to_dict() for ref in self.evidence],
            "social_profiles": [item.to_dict() for item in self.social_profiles],
            "images": [item.to_dict() for item in self.images],
            "public_contacts": [item.to_dict() for item in self.public_contacts],
            "analyst_decisions": [item.to_dict() for item in self.analyst_decisions],
            "sources": [item.to_dict() for item in self.sources],
            "coverage": [item.to_dict() for item in self.coverage],
            "coverage_gaps": self.coverage_gaps,
            "execution": self.execution.to_dict(),
            "graph": self.graph,
            "methodology": self.methodology,
            "limitations": self.limitations,
        }


def _decision_lookup(session: Session, case_id: uuid.UUID) -> dict[str, AnalystDecisionRecord]:
    rows = session.scalars(
        select(AnalystDecisionRecord).where(AnalystDecisionRecord.case_id == case_id)
    )
    return {str(row.subject_id): row for row in rows}


def _social_profile_items(session: Session, case_id: uuid.UUID) -> list[SocialProfileItem]:
    decisions = _decision_lookup(session, case_id)
    rows = session.scalars(
        select(SocialProfile)
        .where(SocialProfile.case_id == case_id)
        .order_by(SocialProfile.confidence.desc())
    )
    items = []
    for row in rows:
        decision = decisions.get(str(row.id))
        items.append(
            SocialProfileItem(
                id=str(row.id),
                platform=row.platform,
                platform_label=row.platform_label,
                handle=row.handle,
                profile_url=row.profile_url,
                display_name=row.display_name,
                accessibility=str(row.accessibility),
                server_fetchable=row.server_fetchable,
                fetch_note=row.fetch_note,
                collector=row.collector,
                evidence_class=row.evidence_class,
                confidence=row.confidence,
                match_reasons=list(row.match_reasons or []),
                mismatch_reasons=list(row.mismatch_reasons or []),
                corroborated_by=list(row.corroborated_by or []),
                candidate_id=str(row.candidate_entity_id) if row.candidate_entity_id else None,
                retrieved_at=row.retrieved_at,
                discovery_method=row.discovery_method,
                discovered_from=row.discovered_from,
                profile_facts=row.profile_facts,
                declared_name=row.declared_name,
                searched_name=row.searched_name,
                name_relationship=row.name_relationship,
                detail_source_url=row.detail_source_url,
                detail_note=row.detail_note,
                analyst_decision=str(decision.decision) if decision else None,
                analyst_note=decision.note if decision else None,
            )
        )
    return items


def _image_items(session: Session, case_id: uuid.UUID) -> list[ImageItem]:
    decisions = _decision_lookup(session, case_id)
    rows = list(
        session.scalars(
            select(ImageEvidence)
            .where(ImageEvidence.case_id == case_id)
            .order_by(ImageEvidence.created_at.desc())
        )
    )
    # Resolved once rather than per row: an image is filed against a profile and
    # a candidate, and a report that prints only a URL makes the reader go
    # looking for both.
    profiles = {
        profile.id: profile
        for profile in session.scalars(
            select(SocialProfile).where(SocialProfile.case_id == case_id)
        )
    }
    candidates = {
        entity.id: entity
        for entity in session.scalars(select(Entity).where(Entity.case_id == case_id))
    }
    items = []
    for row in rows:
        decision = decisions.get(str(row.id))
        attributes = row.attributes or {}
        profile = profiles.get(row.social_profile_id) if row.social_profile_id else None
        candidate = candidates.get(row.candidate_entity_id) if row.candidate_entity_id else None
        safe, note = render_safety(row.image_url)
        items.append(
            ImageItem(
                id=str(row.id),
                image_url=row.image_url,
                source_page_url=row.source_page_url,
                platform=row.platform,
                caption=row.caption,
                fetch_state=str(row.fetch_state),
                sha256=row.sha256,
                content_type=row.content_type,
                byte_length=row.byte_length,
                width=row.width,
                height=row.height,
                redirects=list(row.redirects or []),
                final_url=row.final_url,
                fetch_note=row.fetch_note,
                origin=row.origin,
                evidence_class=row.evidence_class,
                candidate_id=str(row.candidate_entity_id) if row.candidate_entity_id else None,
                retrieved_at=row.retrieved_at,
                platform_label=(profile.platform_label if profile else row.platform),
                profile_url=(profile.profile_url if profile else None),
                handle=(profile.handle if profile else None),
                candidate_name=(candidate.display_name if candidate else None),
                candidate_confidence=(candidate.confidence if candidate else None),
                render_safe=safe,
                render_note=note,
                # Carried into the report so the limit travels with the data.
                analysis=str(attributes.get("analysis", "none")),
                biometric_matching=bool(attributes.get("biometric_matching", False)),
                analyst_decision=str(decision.decision) if decision else None,
                analyst_note=decision.note if decision else None,
            )
        )
    return items


def _public_contact_items(session: Session, case_id: uuid.UUID) -> list[PublicContactItem]:
    from app.services.promotion import contacts_for_case

    decisions = _decision_lookup(session, case_id)
    items = []
    for row in contacts_for_case(session, case_id):
        decision = decisions.get(str(row.id))
        items.append(
            PublicContactItem(
                id=str(row.id),
                contact_type=str(row.contact_type),
                value=row.value,
                label=row.label,
                classification=str(row.classification),
                source_name=row.source_name,
                source_url=row.source_url,
                collector=row.collector,
                evidence_class=row.evidence_class,
                confidence=row.confidence,
                confidence_reasons=list(row.confidence_reasons or []),
                extraction_reason=row.extraction_reason,
                candidate_id=str(row.candidate_entity_id) if row.candidate_entity_id else None,
                retrieved_at=row.retrieved_at,
                analyst_decision=str(decision.decision) if decision else None,
                analyst_note=decision.note if decision else None,
            )
        )
    return items


def _decision_items(session: Session, case_id: uuid.UUID) -> list[AnalystDecisionItem]:
    rows = session.scalars(
        select(AnalystDecisionRecord)
        .where(AnalystDecisionRecord.case_id == case_id)
        .order_by(AnalystDecisionRecord.decided_at.desc())
    )
    return [
        AnalystDecisionItem(
            subject_type=str(row.subject_type),
            subject_id=str(row.subject_id),
            decision=str(row.decision),
            note=row.note,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
        )
        for row in rows
    ]


#: Fixed text. These statements describe how the platform works and must appear
#: on every report, so a reader can judge the evidence rather than trust it.
METHODOLOGY = [
    "Collection is limited to information already published by its owner or by a "
    "public registry, retrieved through documented public APIs and protocols.",
    "Each collector runs independently. A collector that fails is recorded as a "
    "failed run and does not remove the results of the others, so this report "
    "may be partial — the Sources section lists what ran and what did not.",
    "Every finding is passed through a privacy filter before storage. Credentials, "
    "financial identifiers and government identification numbers are replaced "
    "rather than stored; precise addresses, coordinates and dates of birth are "
    "suppressed.",
    "Raw responses are stored under their SHA-256 hash, so any claim in this "
    "report can be traced to the artefact it came from and that artefact can be "
    "re-verified against its recorded hash.",
    "Confidence scores are produced by named rules and are always accompanied by "
    "the reasons that produced them.",
]

LIMITATIONS = [
    "A shared username is not evidence of a shared owner. Where accounts on "
    "different platforms use the same handle, this report records SAME_USERNAME "
    "and nothing stronger.",
    "The platform never merges two entities on inference alone. Suggested matches "
    "are presented for a human to accept or reject.",
    "Certificate transparency entries prove that a certificate was issued for a "
    "hostname, not that the host currently resolves or serves content.",
    "Archive coverage begins when a crawler first reached a site, which may be "
    "considerably later than the site's actual creation.",
    "Absence of evidence is not evidence of absence: a collector that was skipped "
    "for a missing credential, or that failed, leaves a gap rather than a negative "
    "result.",
    "Data was accurate at the time of collection shown against each finding; "
    "public records change.",
]

#: Added only to reports for cases that investigate a named person. A name is
#: not an identifier, so every result for one is a candidate until something
#: else connects it, and a report about a person has to say so on its face.
PERSON_LIMITATIONS = [
    "A personal name is not an identifier. Every result found by searching a "
    "name is recorded as a separate candidate, and two candidates sharing a "
    "name are never treated as the same person without independent evidence.",
    "No infrastructure collector (DNS, RDAP, certificate transparency, HTTP "
    "metadata) is run against a person's name, so this report contains no "
    "inferences drawn from doing so.",
]


def build_report(
    session: Session,
    case_id: uuid.UUID,
    *,
    max_classification: Classification = Classification.PERSONAL,
    min_confidence: float = 0.0,
    privacy: PrivacyFilter | None = None,
) -> ReportModel:
    """Gather a case into a :class:`ReportModel`.

    Args:
        max_classification: content above this level is withheld from the
            report, so it can be circulated more widely than the case database.
        min_confidence: findings below this confidence are omitted.
    """
    case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} does not exist")

    privacy = privacy or PrivacyFilter()
    findings = list(
        session.scalars(
            select(Finding)
            .where(Finding.case_id == case_id, Finding.confidence >= min_confidence)
            .order_by(Finding.confidence.desc(), Finding.created_at)
        )
    )
    entities = list(
        session.scalars(
            select(Entity).where(Entity.case_id == case_id).order_by(Entity.confidence.desc())
        )
    )
    relationships = list(
        session.scalars(
            select(Relationship)
            .where(Relationship.case_id == case_id)
            .order_by(Relationship.confidence.desc())
        )
    )
    events = list(
        session.scalars(
            select(TimelineEvent)
            .where(TimelineEvent.case_id == case_id)
            .order_by(TimelineEvent.occurred_at)
        )
    )
    evidence_rows = list(
        session.scalars(
            select(Evidence).where(Evidence.case_id == case_id).order_by(Evidence.retrieved_at)
        )
    )
    runs = list(session.scalars(select(CollectorRun).where(CollectorRun.case_id == case_id)))
    targets = list(session.scalars(select(Target).where(Target.case_id == case_id)))

    finding_items = [_finding_item(finding, privacy, max_classification) for finding in findings]
    graph = build_graph(entities, relationships)

    model = ReportModel(
        case_id=str(case.id),
        case_name=case.name,
        case_description=case.description,
        case_status=str(case.status),
        case_notes=case.notes,
        case_tags=[tag.name for tag in case.tags],
        created_at=case.created_at,
        generated_at=datetime.now(UTC),
        platform_version=__version__,
        targets=[
            {
                "id": str(target.id),
                "type": str(target.type),
                "raw_input": target.raw_input,
                "normalized_value": target.normalized_value,
                "status": str(target.status),
                "notes": target.notes,
            }
            for target in targets
        ],
        findings=finding_items,
        key_findings=finding_items[:KEY_FINDING_LIMIT],
        entities=[_entity_item(entity) for entity in entities],
        relationships=[_relationship_item(edge) for edge in relationships],
        timeline=[_timeline_item(event) for event in events],
        evidence=[_evidence_ref(row) for row in evidence_rows],
        social_profiles=_social_profile_items(session, case_id),
        public_contacts=_public_contact_items(session, case_id),
        images=_image_items(session, case_id),
        analyst_decisions=_decision_items(session, case_id),
        sources=_sources(runs, findings),
        coverage=_coverage_items(session, case_id, runs, findings),
        execution=_execution_summary(session, case_id, runs, findings),
        graph=graph_summary(graph),
        methodology=list(METHODOLOGY),
        limitations=list(LIMITATIONS),
        policy={
            "max_classification": str(max_classification),
            "min_confidence": min_confidence,
            "withheld_findings": sum(1 for item in finding_items if item.withheld),
            "redacted_findings": sum(1 for item in finding_items if item.redacted),
        },
    )
    model.counts = {
        "targets": len(targets),
        "findings": len(finding_items),
        "entities": len(entities),
        "relationships": len(relationships),
        "evidence": len(evidence_rows),
        "timeline_events": len(events),
        # Every run the case has recorded, which is not the same number as the
        # collectors the latest execution used. `execution` tells them apart.
        "collector_runs": len(runs),
        "investigation_executions": model.execution.executions,
        "latest_execution_collectors": model.execution.collectors_attempted,
    }
    model.coverage_gaps = summarise_gaps(model.coverage)
    model.confidence = _confidence_summary(finding_items, relationships)
    model.confidence["timeline"] = timeline_summary(events)
    if any(target.type is TargetType.PERSON for target in targets):
        model.limitations = list(PERSON_LIMITATIONS) + model.limitations
    model.executive_summary = _executive_summary(model, runs)
    return model


def _finding_item(
    finding: Finding, privacy: PrivacyFilter, max_classification: Classification
) -> FindingItem:
    data, withheld = privacy.filter_for_export(
        dict(finding.data or {}),
        classification=finding.classification,
        max_level=max_classification,
    )
    return FindingItem(
        id=str(finding.id),
        kind=str(finding.kind),
        title=finding.title,
        summary=finding.summary,
        data=data,
        collector=finding.collector,
        source_url=finding.source_url,
        confidence=finding.confidence,
        confidence_reasons=list(finding.confidence_reasons or []),
        classification=str(finding.classification),
        redacted=finding.redacted,
        withheld=withheld,
        observed_at=finding.observed_at,
        evidence=[_evidence_ref(row) for row in (finding.evidence or [])],
    )


def _entity_item(entity: Entity) -> EntityItem:
    return EntityItem(
        id=str(entity.id),
        type=str(entity.type),
        display_name=entity.display_name,
        canonical_value=entity.canonical_value,
        aliases=list(entity.aliases or []),
        attributes=dict(entity.attributes or {}),
        confidence=entity.confidence,
        confidence_reasons=list(entity.confidence_reasons or []),
        source_finding_ids=[str(finding.id) for finding in (entity.sources or [])],
    )


def _relationship_item(edge: Relationship) -> RelationshipItem:
    return RelationshipItem(
        id=str(edge.id),
        type=str(edge.type),
        source_label=edge.source_entity.display_name if edge.source_entity else "?",
        target_label=edge.target_entity.display_name if edge.target_entity else "?",
        source_id=str(edge.source_entity_id),
        target_id=str(edge.target_entity_id),
        confidence=edge.confidence,
        strength=str(edge.strength),
        confidence_reasons=list(edge.confidence_reasons or []),
        collector=edge.collector,
        evidence_finding_ids=[str(finding.id) for finding in (edge.evidence or [])],
    )


def _evidence_ref(row: Evidence) -> EvidenceRef:
    return EvidenceRef(
        id=str(row.id),
        sha256=row.sha256,
        source_url=row.source_url,
        collector=row.collector,
        retrieved_at=row.retrieved_at,
        excerpt=row.excerpt,
        redacted=row.redacted,
    )


def _timeline_item(event: TimelineEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "kind": event.kind,
        "title": event.title,
        "description": event.description,
        "collector": event.collector,
        "source_url": event.source_url,
        "confidence": round(event.confidence, 3),
        "finding_id": str(event.finding_id) if event.finding_id else None,
    }


def _sources(runs: list[CollectorRun], findings: list[Finding]) -> list[SourceItem]:
    """Summarise what ran, from the recorded runs.

    Attribution comes from the run rows, recorded when the collectors actually
    ran. A report describes a past investigation, so it must not depend on
    which collectors the rendering process happens to have loaded — and
    rendering must never mutate that set.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for run in runs:
        entry = grouped.setdefault(
            run.collector,
            {
                "version": run.collector_version,
                "attribution": run.source_attribution or "",
                "runs": 0,
                "ok": 0,
                "failed": 0,
                "skipped": 0,
            },
        )
        entry["runs"] += 1
        if not entry["attribution"] and run.source_attribution:
            entry["attribution"] = run.source_attribution
        if run.status in {RunStatus.SUCCESS, RunStatus.PARTIAL}:
            entry["ok"] += 1
        elif run.status is RunStatus.SKIPPED:
            entry["skipped"] += 1
        else:
            entry["failed"] += 1

    per_collector_findings: dict[str, int] = {}
    for finding in findings:
        per_collector_findings[finding.collector] = (
            per_collector_findings.get(finding.collector, 0) + 1
        )

    items = []
    for collector, entry in sorted(grouped.items()):
        items.append(
            SourceItem(
                collector=collector,
                version=entry["version"],
                runs=entry["runs"],
                successes=entry["ok"],
                failures=entry["failed"],
                skipped=entry["skipped"],
                findings=per_collector_findings.get(collector, 0),
                attribution=entry["attribution"],
            )
        )
    return items


def _execution_summary(
    session: Session,
    case_id: uuid.UUID,
    runs: list[CollectorRun],
    findings: list[Finding],
) -> ExecutionSummary:
    """Split the latest execution from the case's history.

    A ``Job`` is an execution, and ``CollectorRun.job_id`` already says which one
    a run belonged to — so no new table is needed to answer "what did this run
    do?". Runs with no job (a direct engine call, no queue) are grouped as one
    unattributed execution rather than being dropped: they happened.
    """
    summary = ExecutionSummary(historical_runs=len(runs))
    jobs = list(
        session.scalars(select(Job).where(Job.case_id == case_id).order_by(Job.created_at.desc()))
    )
    unattributed = [run for run in runs if run.job_id is None]
    summary.executions = len(jobs) + (1 if unattributed else 0)
    if not runs:
        return summary

    latest = jobs[0] if jobs else None
    if latest is not None:
        current = [run for run in runs if run.job_id == latest.id]
        summary.latest_job_id = str(latest.id)
        summary.latest_state = str(latest.state)
        summary.latest_started_at = latest.started_at
        summary.latest_finished_at = latest.finished_at
    else:
        current = unattributed
    if not current:
        # The newest job recorded no runs of its own (queued, cancelled before
        # work began). Saying "0 collectors" would be accurate but unhelpful, so
        # fall back to the newest runs that do exist and say which job they were.
        newest = max(runs, key=lambda run: run.created_at)
        current = [run for run in runs if run.job_id == newest.job_id]
        summary.latest_job_id = str(newest.job_id) if newest.job_id else None

    collectors = {run.collector for run in current}
    summary.collectors_attempted = len(collectors)
    summary.successful = sum(
        1 for run in current if run.status in {RunStatus.SUCCESS, RunStatus.PARTIAL}
    )
    summary.skipped = sum(1 for run in current if run.status is RunStatus.SKIPPED)
    summary.failed = sum(
        1 for run in current if run.status in {RunStatus.FAILED, RunStatus.TIMEOUT}
    )
    # Findings are deduplicated across reruns, so they do not belong to one
    # execution and cannot be counted per execution. What can be said honestly is
    # how many findings the collectors in this execution account for — stated as
    # that, rather than dressed up as "findings this run produced".
    summary.findings = sum(1 for finding in findings if finding.collector in collectors)
    return summary


def _latest_runs(
    session: Session, case_id: uuid.UUID, runs: list[CollectorRun]
) -> list[CollectorRun]:
    """The runs belonging to the most recent execution."""
    latest_job = session.scalar(
        select(Job).where(Job.case_id == case_id).order_by(Job.created_at.desc()).limit(1)
    )
    if latest_job is not None:
        current = [run for run in runs if run.job_id == latest_job.id]
        if current:
            return current
    unattributed = [run for run in runs if run.job_id is None]
    if unattributed:
        return unattributed
    return runs


def _coverage_items(
    session: Session,
    case_id: uuid.UUID,
    runs: list[CollectorRun],
    findings: list[Finding],
) -> list[CoverageItem]:
    """What each source family can honestly be said to have contributed.

    Read from the *latest* execution's runs. A collector that succeeded three
    reruns ago and was skipped today is skipped today, and a report describing
    the current investigation must say so.
    """
    current = _latest_runs(session, case_id, runs)
    per_collector: dict[str, int] = {}
    for finding in findings:
        per_collector[finding.collector] = per_collector.get(finding.collector, 0) + 1

    items: list[CoverageItem] = []
    by_collector: dict[str, list[CollectorRun]] = {}
    for run in current:
        by_collector.setdefault(run.collector, []).append(run)

    for collector, collector_runs in sorted(by_collector.items()):
        found = per_collector.get(collector, 0)
        # Best outcome across this execution's runs of the collector: a success
        # against one target is a search that happened, whatever another target did.
        states = [state_for_run(run.status, found) for run in collector_runs]
        state = min(states, key=_coverage_rank)
        detail = ""
        if state is CoverageState.FAILED:
            failure = next(
                (run for run in collector_runs if run.error_message or run.error_type), None
            )
            if failure is not None:
                detail = f"{failure.error_type}: {(failure.error_message or '')[:200]}".strip(": ")
        elif state is CoverageState.SKIPPED:
            note = next((run for run in collector_runs if run.error_message), None)
            detail = (note.error_message or "")[:200] if note else ""
        items.append(
            CoverageItem(
                source=collector,
                display_name=collector,
                state=state,
                detail=detail,
                findings=found,
                runs=len(collector_runs),
            )
        )

    searched = {item.source for item in items}
    ingest = _ingest_coverage(session, case_id)
    if ingest is not None:
        items.append(ingest)
    images = len(_image_items(session, case_id))
    items.append(
        image_search_coverage(
            images=images,
            provider_configured=bool(
                ingest and ingest.state is not CoverageState.PROVIDER_NOT_CONFIGURED
            ),
        )
    )
    items.extend(item for item in manual_only_platforms() if item.source not in searched)
    return items


def _ingest_coverage(session: Session, case_id: uuid.UUID) -> CoverageItem | None:
    """The public-web channel, from what the last ingestion run recorded.

    Read off the job's stored result rather than re-deriving it: the report
    describes what happened, and what happened was recorded when it happened.
    """
    from app.services.providers.search import get_search_provider

    latest = session.scalar(
        select(Job).where(Job.case_id == case_id).order_by(Job.created_at.desc()).limit(1)
    )
    recorded = ((latest.result if latest else None) or {}).get("search_ingest")
    if isinstance(recorded, dict):
        return web_search_coverage(
            provider=str(recorded.get("provider", "unknown")),
            configured=bool(recorded.get("configured")),
            queries_run=int(recorded.get("queries_run", 0)),
            results_stored=int(recorded.get("results_stored", 0)),
            reason=str(recorded.get("reason") or ""),
            failures=len(recorded.get("failures") or []),
        )
    try:
        provider = get_search_provider()
    except Exception:
        return web_search_coverage(
            provider="unknown", configured=False, queries_run=0, results_stored=0
        )
    available, reason = provider.is_available()
    return web_search_coverage(
        provider=provider.key,
        configured=available,
        queries_run=0,
        results_stored=0,
        reason=reason,
    )


#: Worst-first, so one collector's best outcome across targets wins.
_COVERAGE_RANK = {
    CoverageState.FOUND: 0,
    CoverageState.NO_MATCH_RETURNED: 1,
    CoverageState.SKIPPED: 2,
    CoverageState.FAILED: 3,
    CoverageState.NOT_SEARCHED: 4,
    CoverageState.MANUAL_REVIEW_AVAILABLE: 5,
    CoverageState.PROVIDER_NOT_CONFIGURED: 6,
}


def _coverage_rank(state: CoverageState) -> int:
    return _COVERAGE_RANK.get(state, 9)


def _confidence_summary(
    findings: list[FindingItem], relationships: list[Relationship]
) -> dict[str, Any]:
    buckets = {"LIKELY_MATCH": 0, "PROBABLE_MATCH": 0, "POSSIBLE_MATCH": 0, "WEAK_ASSOCIATION": 0}
    for finding in findings:
        buckets[finding.strength] += 1
    edge_buckets = dict.fromkeys(buckets, 0)
    for edge in relationships:
        # Derived from the score rather than read from the stored band, so the
        # summary can never disagree with the confidence shown on the edge.
        edge_buckets[str(classify(edge.confidence))] += 1

    scores = [finding.confidence for finding in findings]
    return {
        "finding_bands": buckets,
        "relationship_bands": edge_buckets,
        "mean_finding_confidence": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "high_confidence_findings": sum(1 for score in scores if score >= 0.8),
        "bands": {
            "LIKELY_MATCH": ">= 0.90",
            "PROBABLE_MATCH": "0.70 - 0.89",
            "POSSIBLE_MATCH": "0.50 - 0.69",
            "WEAK_ASSOCIATION": "< 0.50",
        },
    }


def _executive_summary(model: ReportModel, runs: list[CollectorRun]) -> list[str]:
    """Plain statements of what was examined and what came back."""
    lines: list[str] = []
    target_desc = ", ".join(
        f"{target['normalized_value']} ({target['type']})" for target in model.targets[:5]
    )
    if len(model.targets) > 5:
        target_desc += f" and {len(model.targets) - 5} more"
    lines.append(
        f"This case examined {model.counts['targets']} target(s)"
        + (f": {target_desc}." if target_desc else ".")
    )
    lines.append(
        f"{model.execution.collectors_attempted} collector(s) ran in the latest execution, "
        f"producing {model.counts['findings']} finding(s) across the case supported by "
        f"{model.counts['evidence']} stored artefact(s). The case has been investigated "
        f"{model.execution.executions} time(s); see Investigation executions."
    )
    # The gap lines come first among the caveats and are never omitted. A summary
    # that reports "0 findings" without saying which channels were never searched
    # invites the reader to conclude there is nothing to find, which is the single
    # most damaging thing this report could imply.
    lines.extend(model.coverage_gaps)
    if not model.counts["findings"] and model.coverage_gaps:
        lines.append(
            "No findings were recorded. Given the gaps above, that is not a statement that "
            "no public record exists — it is a statement about what was searched."
        )
    if model.counts["entities"]:
        lines.append(
            f"{model.counts['entities']} entities and {model.counts['relationships']} "
            f"relationships were derived from those findings."
        )
    high = model.confidence.get("high_confidence_findings", 0)
    if high:
        lines.append(f"{high} finding(s) are rated 0.80 or above.")

    failed = [run for run in runs if run.status in {RunStatus.FAILED, RunStatus.TIMEOUT}]
    skipped = [run for run in runs if run.status is RunStatus.SKIPPED]
    if failed:
        lines.append(
            f"{len(failed)} collector run(s) failed; this report is partial to that extent."
        )
    if skipped:
        lines.append(
            f"{len(skipped)} collector run(s) were skipped, usually because an API key is not "
            f"configured. See the Sources section."
        )
    if model.policy.get("withheld_findings"):
        lines.append(
            f"{model.policy['withheld_findings']} finding(s) were withheld from this report by "
            f"the export privacy policy ({model.policy['max_classification']} and below)."
        )
    if not model.counts["findings"]:
        lines.append(
            "No findings were produced. Nothing in this report should be read as a "
            "negative result."
        )
    return lines
