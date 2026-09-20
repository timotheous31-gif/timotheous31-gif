"""The investigator-facing presentation of a report.

A *view* over :class:`app.reporting.model.ReportModel`, and nothing more. There
is no second report model here: every value this module returns was computed by
the pipeline and is already in the JSON export. What this module decides is
**order, grouping and framing** — which is precisely what the Markdown and JSON
renderers, written for an engineer reading the whole case, do not decide well
for an investigator handing a document to a client.

Three rules hold throughout, and they are what separates a dossier from a
narrative:

**Nothing is asserted that the model does not carry.** No sentence here
summarises, infers, or smooths. Where the model has no value, the section says
so rather than filling the gap — "no public contact information was recovered"
is a finding, and inventing a plausible one would be a fabrication.

**Every statement is labelled with what kind of claim it is.** See
:class:`Basis`. A reader must never have to guess whether a line is a fact a
source published, a number this platform computed, a judgement a human made, or
a lead nobody has resolved. Those four things look identical in prose and are
entirely different in weight.

**The number is a correlation score.** Never "confidence", never a percentage.
It is a combination of named rules, uncalibrated against any measured outcome,
so presenting it as a probability would state a likelihood nobody computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.correlation.suppression import REJECTED
from app.reporting.model import (
    AnalystDecisionItem,
    EntityItem,
    ImageItem,
    ReportModel,
)


class Basis(StrEnum):
    """What kind of claim a line in the report is.

    The single most important thing this presentation layer adds. A dossier
    that mixes these four is how an automated score becomes, three readers
    later, something somebody believed a person had confirmed.
    """

    #: A source published this. The strongest thing here, and still only as good
    #: as the source: it is a record of what was said, not of what is true.
    SOURCED_FACT = "SOURCED_FACT"
    #: This platform's rules associated two things. Not a probability, not an
    #: identification, and never on its own a reason to act.
    CORRELATION = "CORRELATION"
    #: A human analyst recorded a judgement. Carries their name and the moment.
    ANALYST_DECISION = "ANALYST_DECISION"
    #: Something worth checking that nothing has resolved. Explicitly open.
    UNRESOLVED_LEAD = "UNRESOLVED_LEAD"
    #: A statement about *this investigation* rather than about the subject:
    #: what ran, what was found, what was withheld, what failed.
    #:
    #: Its own basis because the alternative was worse. These lines were being
    #: labelled as sourced facts, which reads as "a source published that one
    #: collector run failed" — nonsense, and exactly the kind of quiet
    #: mislabelling the basis vocabulary exists to prevent.
    CASE_RECORD = "CASE_RECORD"


#: How each basis is introduced to a reader who has never seen this report
#: before. Printed in the dossier itself, not left to a footnote.
BASIS_MEANINGS: dict[str, str] = {
    Basis.SOURCED_FACT: (
        "Published by the cited source and stored as evidence. A record of what "
        "the source stated, which is not the same as a verified truth."
    ),
    Basis.CORRELATION: (
        "Computed by this platform from named rules. A correlation score is not "
        "a probability and identifies nobody on its own."
    ),
    Basis.ANALYST_DECISION: (
        "A human analyst's recorded judgement, stored separately from the "
        "automated score and never merged into it."
    ),
    Basis.UNRESOLVED_LEAD: (
        "Worth checking. Nothing has corroborated or ruled it out, and it "
        "should not be relied on."
    ),
    Basis.CASE_RECORD: (
        "A statement about this investigation rather than about the subject — "
        "what was run, what was found, what was withheld. Verifiable against "
        "the case's own records."
    ),
}

BASIS_LABELS: dict[str, str] = {
    Basis.SOURCED_FACT: "Sourced fact",
    Basis.CORRELATION: "Correlation",
    Basis.ANALYST_DECISION: "Analyst decision",
    Basis.UNRESOLVED_LEAD: "Unresolved lead",
    Basis.CASE_RECORD: "Case record",
}

#: Printed wherever a picture of a person appears. The platform performs no
#: image comparison of any kind, and a report must not let a reader assume
#: otherwise from the mere presence of a photograph.
IMAGE_CAPTION_RULE = (
    "Shown as page context only. This platform performs no facial recognition, "
    "no image comparison and no biometric matching of any kind; the picture is "
    "presented because the cited page published it beside this candidate, not "
    "because anything here identified the person in it."
)


@dataclass(slots=True)
class Statement:
    """One line of the dossier, with the basis it rests on.

    ``source`` and ``source_url`` are the citation. ``detail`` is a second line
    a reader may need — a reason, a caveat, a retrieval date — and is never a
    different claim from ``text``.
    """

    text: str
    basis: str
    source: str | None = None
    source_url: str | None = None
    detail: str | None = None
    retrieved_at: datetime | None = None

    @property
    def basis_label(self) -> str:
        return BASIS_LABELS.get(self.basis, self.basis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "basis": str(self.basis),
            "source": self.source,
            "source_url": self.source_url,
            "detail": self.detail,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
        }


@dataclass(slots=True)
class Section:
    """One numbered section of the dossier.

    ``empty_note`` is what the section says when it has nothing — deliberately
    required rather than optional, because a silently absent section reads as
    "not applicable" when the truth is usually "searched, found nothing", and
    those are different findings.
    """

    key: str
    title: str
    #: One sentence telling the reader what this section is and is not.
    summary: str
    statements: list[Statement] = field(default_factory=list)
    empty_note: str = "Nothing was recorded for this section."
    #: Rows for a section that reads better as a table than as sentences.
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.statements and not self.rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "summary": self.summary,
            "statements": [item.to_dict() for item in self.statements],
            "rows": self.rows,
            "empty_note": self.empty_note,
            "is_empty": self.is_empty,
        }


@dataclass(slots=True)
class ProfileImage:
    """A public picture shown beside the subject profile.

    Only ever a *candidate's* picture, captioned with where it came from and
    when it was retrieved. The caption is not decoration: a photograph in an
    investigation file invites exactly the inference this platform cannot make.
    """

    image_url: str
    source_page_url: str
    candidate_name: str | None
    platform_label: str | None
    retrieved_at: datetime | None
    origin: str
    caption_rule: str = IMAGE_CAPTION_RULE

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_url": self.image_url,
            "source_page_url": self.source_page_url,
            "candidate_name": self.candidate_name,
            "platform_label": self.platform_label,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "origin": self.origin,
            "caption_rule": self.caption_rule,
        }


@dataclass(slots=True)
class Dossier:
    """The whole investigator-facing document, as sections."""

    model: ReportModel
    sections: list[Section] = field(default_factory=list)
    profile_image: ProfileImage | None = None
    #: Sections that belong after the signature: retained detail, not findings.
    appendix: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": [item.to_dict() for item in self.sections],
            "appendix": [item.to_dict() for item in self.appendix],
            "profile_image": self.profile_image.to_dict() if self.profile_image else None,
        }


# --- helpers ----------------------------------------------------------------


def _is_candidate(entity: EntityItem) -> bool:
    return entity.attributes.get("role") == "candidate"


def _is_subject(entity: EntityItem) -> bool:
    return entity.attributes.get("role") == "subject"


def _counts_toward_the_body(entity: EntityItem) -> bool:
    """Whether a candidate's attributes may speak for the subject in the body.

    A candidate an analyst ruled out must not keep contributing. Without this,
    a rejected record's city appeared under "Geographic associations" as
    *"Yes — matches an anchor you supplied"*, two sections above the decision
    rejecting the only record that claimed it. The reader is told a place is
    corroborated and, separately, that it is not the subject's — and the
    aggregate is the louder of the two.

    Rejected candidates keep their attributes everywhere they are already
    reported: their own appendix row, the analyst-decisions section, and the
    JSON export. Nothing is deleted; it stops being asserted on the subject's
    behalf.
    """
    return _is_candidate(entity) and entity.presentation != REJECTED


def _candidate_name(entity: EntityItem) -> str:
    return str(entity.attributes.get("candidate_name") or entity.display_name)


def _score(value: float) -> str:
    """A correlation score, as a score. Never a percentage."""
    return f"{value:.2f}"


def _link(url: str | None) -> dict[str, str] | None:
    """A URL as a table cell: short enough to read, complete enough to follow.

    A full URL in a narrow column wraps one character per line and makes the
    table unreadable. The label is shortened; the href is not, so a reader
    following the citation still lands exactly where the evidence is.
    """
    if not url:
        return None
    text = str(url)
    label = text.split("://", 1)[-1]
    if len(label) > 48:
        label = label[:30] + "…" + label[-14:]
    return {"href": text, "label": label}


def _when(value: datetime | None) -> str | None:
    """A timestamp a person reads, not one a database prints."""
    return value.strftime("%d %b %Y %H:%M UTC") if value else None


def _person_targets(model: ReportModel) -> list[dict[str, Any]]:
    return [target for target in model.targets if str(target.get("type")) == "PERSON"]


def _decision_for(model: ReportModel, subject_id: str) -> AnalystDecisionItem | None:
    for decision in model.analyst_decisions:
        if str(decision.subject_id) == subject_id:
            return decision
    return None


def profile_image_for(model: ReportModel) -> ProfileImage | None:
    """The one public picture worth showing beside the subject profile.

    Chosen, not invented: the first image that is safe to draw and is filed
    against a candidate, preferring one an analyst confirmed, then the
    strongest-scoring candidate. A picture with no candidate is evidence
    nobody has attributed, and putting it under "subject profile" would be the
    report making an attribution the case does not hold.
    """
    if not _person_targets(model):
        return None

    usable = [
        image
        for image in model.images
        if image.render_safe and image.image_url and image.candidate_id
    ]
    if not usable:
        return None

    def rank(image: ImageItem) -> tuple[int, float]:
        confirmed = image.analyst_decision == "CONFIRMED"
        return (0 if confirmed else 1, -(image.candidate_confidence or 0.0))

    best = sorted(usable, key=rank)[0]
    return ProfileImage(
        image_url=best.image_url,
        source_page_url=best.source_page_url,
        candidate_name=best.candidate_name,
        platform_label=best.platform_label,
        retrieved_at=best.retrieved_at,
        origin=best.origin,
    )


# --- sections ---------------------------------------------------------------


def _executive_summary(model: ReportModel) -> Section:
    section = Section(
        key="executive_summary",
        title="Executive summary",
        summary=(
            "What this investigation established, and on what basis. Every line "
            "below is repeated in full, with its citation, in the section named."
        ),
        empty_note="This investigation produced no summary statements.",
    )
    # Every line here is generated by the platform from the case's own records —
    # how many targets, how many findings, what was withheld, what failed. None
    # of it is a claim a source made about the subject, and labelling it as one
    # would be the report's first lie.
    for line in model.executive_summary:
        section.statements.append(Statement(text=line, basis=Basis.CASE_RECORD))
    return section


def _subject_profile(model: ReportModel) -> Section:
    section = Section(
        key="subject_profile",
        title="Subject profile",
        summary=(
            "What the investigator supplied, and what the case holds about the "
            "subject as named. The subject is defined by the target, never by a "
            "source's spelling of a name."
        ),
        empty_note="No subject target was recorded for this case.",
    )
    for target in model.targets:
        section.rows.append(
            {
                "type": str(target.get("type")),
                "value": target.get("raw_input") or target.get("normalized_value"),
                "normalized": target.get("normalized_value"),
                "status": str(target.get("status")),
                "notes": target.get("notes"),
            }
        )
    for entity in model.entities:
        if not _is_subject(entity):
            continue
        section.statements.append(
            Statement(
                text=f"Subject entity: {entity.display_name}",
                basis=Basis.CASE_RECORD,
                detail=f"Canonical value {entity.canonical_value}",
            )
        )
    return section


def _key_findings(model: ReportModel) -> Section:
    section = Section(
        key="key_findings",
        title="Key findings",
        summary=(
            "The highest-scoring findings, each citing the stored artefact that "
            "supports it. A finding is something a source published; the score "
            "beside it is this platform's, not the source's."
        ),
        empty_note=(
            "No finding met the report's threshold. That is not the same as "
            "nothing being found — see Source coverage for what was searched."
        ),
    )
    for finding in model.key_findings:
        section.statements.append(
            Statement(
                text=finding.title,
                basis=Basis.SOURCED_FACT,
                source=finding.collector,
                source_url=getattr(finding, "source_url", None),
                detail=(
                    f"Correlation score {_score(finding.confidence)}"
                    + (f" · {finding.summary}" if getattr(finding, "summary", None) else "")
                ),
            )
        )
    return section


def _identity_assessment(model: ReportModel) -> tuple[Section, Section]:
    """Candidates, and the weak ones held back for the appendix.

    Returns the main section and the appendix section together because the
    split between them is one decision made once: a candidate is in the body or
    in the appendix, never both, and never neither.
    """
    folded_ids = {item.id for item in model.low_confidence_candidates}
    candidates = [entity for entity in model.entities if _is_candidate(entity)]

    main = Section(
        key="identity_assessment",
        title="Identity and candidate assessment",
        summary=(
            "Records carrying the subject's name, and what connects each to the "
            "subject beyond that name. A name is shared by many people and is "
            "not an identifier: nothing in this section is an identification."
        ),
        empty_note="No candidate records were produced for this subject.",
    )
    appendix = Section(
        key="low_confidence_candidates",
        title="Low-confidence candidates (retained)",
        summary=(
            "Records that carry the name, or part of it, and nothing else. "
            "Retained in full — scored, stored and exported exactly as any other "
            "candidate — and held here so they do not crowd the findings above."
        ),
        empty_note="No candidates were set aside.",
    )

    for entity in candidates:
        anchors = [str(item) for item in (entity.attributes.get("corroborated_by") or [])]
        decision = _decision_for(model, entity.id)
        target = appendix if entity.id in folded_ids else main

        if decision is not None:
            basis = Basis.ANALYST_DECISION
            detail = f"Analyst recorded {decision.decision}"
            if getattr(decision, "note", None):
                detail += f" — {decision.note}"
        elif anchors:
            basis = Basis.CORRELATION
            detail = "Corroborated by " + ", ".join(sorted(anchors))
        else:
            basis = Basis.UNRESOLVED_LEAD
            detail = "Nothing beyond the name connects this record to the subject"

        target.rows.append(
            {
                "name": _candidate_name(entity),
                "score": _score(entity.confidence),
                "source": entity.attributes.get("source_label") or entity.attributes.get("source"),
                "corroborated_by": ", ".join(sorted(anchors)) or None,
                "assessment": detail,
                "url": _link(entity.attributes.get("reference_url")),
                "basis": str(basis),
            }
        )
    return main, appendix


def _profiles(model: ReportModel) -> Section:
    section = Section(
        key="profiles",
        title="Public professional and social profiles",
        summary=(
            "Public pages attributed to a candidate, with how each was found. "
            "Attribution is to a candidate record, not to the subject."
        ),
        empty_note="No public profiles were recovered.",
    )
    for profile in model.social_profiles:
        section.rows.append(
            {
                "platform": profile.platform_label or profile.platform,
                "handle": profile.handle,
                "url": _link(profile.profile_url),
                "score": _score(profile.confidence),
                "corroborated_by": ", ".join(sorted(profile.corroborated_by)) or None,
                "found_by": profile.discovery_method,
                "retrieved": _when(profile.retrieved_at),
                "basis": str(
                    Basis.CORRELATION if profile.corroborated_by else Basis.UNRESOLVED_LEAD
                ),
            }
        )
    return section


def _contacts(model: ReportModel) -> Section:
    section = Section(
        key="contacts",
        title="Public contact information",
        summary=(
            "Contact points a source published openly. Published is not the same "
            "as current, and not the same as an invitation to use it."
        ),
        empty_note="No public contact information was recovered.",
    )
    for contact in model.public_contacts:
        section.rows.append(
            {
                "type": contact.contact_type,
                "value": contact.value,
                "label": contact.label,
                "classification": contact.classification,
                "source": contact.source_name,
                "url": _link(contact.source_url),
                "score": _score(contact.confidence),
                "basis": str(Basis.SOURCED_FACT),
            }
        )
    return section


def _affiliations(model: ReportModel) -> Section:
    """Organisations, read off the candidates that carry them.

    Deduplicated by name so one organisation named by three sources is one row
    with three sources, rather than three rows that read as three facts.
    """
    section = Section(
        key="affiliations",
        title="Organisations and affiliations",
        summary=(
            "Organisations a source attributes to a candidate record. An "
            "affiliation corroborates a candidate only where the investigator "
            "supplied the same organisation independently."
        ),
        empty_note="No organisational affiliation was published by any source.",
    )
    seen: dict[str, dict[str, Any]] = {}
    for entity in model.entities:
        if not _counts_toward_the_body(entity):
            continue
        anchors = {str(item) for item in (entity.attributes.get("corroborated_by") or [])}
        for name in entity.attributes.get("affiliations") or []:
            key = str(name).strip().lower()
            if not key:
                continue
            row = seen.setdefault(
                key,
                {
                    "name": str(name),
                    "candidates": [],
                    "corroborated": False,
                    "basis": str(Basis.SOURCED_FACT),
                },
            )
            row["candidates"].append(_candidate_name(entity))
            if "affiliation" in anchors:
                row["corroborated"] = True
                row["basis"] = str(Basis.CORRELATION)
    section.rows = [_finish_group(row, "name") for row in sorted(seen.values(), key=_by_name)]
    return section


def _geography(model: ReportModel) -> Section:
    section = Section(
        key="geography",
        title="Geographic associations",
        summary=(
            "Places a source attributes to a candidate record. A place is not a "
            "residence, not a current location, and among the weakest anchors "
            "there is — very many unrelated people share one."
        ),
        empty_note="No geographic association was published by any source.",
    )
    seen: dict[str, dict[str, Any]] = {}
    for entity in model.entities:
        if not _counts_toward_the_body(entity):
            continue
        anchors = {str(item) for item in (entity.attributes.get("corroborated_by") or [])}
        for place in entity.attributes.get("locations") or []:
            key = str(place).strip().lower()
            if not key:
                continue
            row = seen.setdefault(
                key,
                {
                    "place": str(place),
                    "candidates": [],
                    "corroborated": False,
                    "basis": str(Basis.SOURCED_FACT),
                },
            )
            row["candidates"].append(_candidate_name(entity))
            if "location" in anchors:
                row["corroborated"] = True
                row["basis"] = str(Basis.CORRELATION)
    section.rows = [_finish_group(row, "place") for row in sorted(seen.values(), key=_by_place)]
    return section


def _by_name(row: dict[str, Any]) -> str:
    return str(row["name"]).lower()


def _by_place(row: dict[str, Any]) -> str:
    return str(row["place"]).lower()


def _finish_group(row: dict[str, Any], key: str) -> dict[str, Any]:
    """Present a grouped row: named candidates deduplicated, corroboration in words.

    Two candidate records spelled the same name are two records, but printing
    "Sara Samara, Sara Samara" in a cell tells a reader nothing and looks like a
    bug. The count does the work the repetition was failing to do.
    """
    names = sorted(set(row.pop("candidates")))
    shown = ", ".join(names)
    count = len(names)
    return {
        key: row[key],
        "named by": f"{shown}" + (f" ({count} records)" if count > 1 else ""),
        "corroborates the subject": (
            "Yes — matches an anchor you supplied"
            if row["corroborated"]
            else "No — published by the source only"
        ),
        "basis": row["basis"],
    }


def _relationships(model: ReportModel) -> Section:
    section = Section(
        key="relationships",
        title="Relationship highlights",
        summary=(
            "Typed links between entities, each with the reasons the platform "
            "recorded for it. A link is an association, never an assertion that "
            "two records are the same person."
        ),
        empty_note="No relationships were derived.",
    )
    for edge in model.relationships:
        section.rows.append(
            {
                "from": edge.source_label,
                "type": str(edge.type),
                "to": edge.target_label,
                "score": _score(edge.confidence),
                "reasons": list(edge.confidence_reasons),
                "basis": str(Basis.CORRELATION),
            }
        )
    return section


def _timeline(model: ReportModel) -> Section:
    section = Section(
        key="timeline",
        title="Timeline and notable events",
        summary=(
            "Dated events, as the sources stated them. A date is the source's "
            "claim about when something happened, not this platform's."
        ),
        empty_note="No dated events were recorded.",
    )
    section.rows = list(model.timeline)
    return section


def _decisions(model: ReportModel) -> Section:
    section = Section(
        key="analyst_decisions",
        title="Analyst decisions",
        summary=(
            "Judgements recorded by a human, kept separate from every automated "
            "score in this report. A decision never edits a correlation score, "
            "and a score never stands in for a decision."
        ),
        empty_note="No analyst has recorded a decision on this case.",
    )
    # A decision stores a subject id. Naming the subject here is the whole
    # point of the section — a reader cannot audit "CONFIRMED 3f8e7549".
    names = {
        entity.id: _candidate_name(entity) for entity in model.entities if _is_candidate(entity)
    }
    for decision in model.analyst_decisions:
        subject = names.get(str(decision.subject_id), str(decision.subject_id))
        section.statements.append(
            Statement(
                text=f"{decision.decision} — {subject}",
                basis=Basis.ANALYST_DECISION,
                source=decision.decided_by,
                detail=decision.note,
                retrieved_at=decision.decided_at,
            )
        )
    return section


def _coverage(model: ReportModel) -> Section:
    section = Section(
        key="coverage",
        title="Source coverage",
        summary=(
            "What was searched, what was not, and why. The section that stops "
            "'nothing was found' from being read as 'there is nothing to find'."
        ),
        empty_note="No coverage was recorded for this case.",
    )
    for item in model.coverage:
        section.rows.append(
            {
                "source": item.display_name or item.source,
                "state": str(item.state),
                "findings": item.findings,
                "detail": item.detail,
            }
        )
    for gap in model.coverage_gaps:
        section.statements.append(Statement(text=gap, basis=Basis.CASE_RECORD))
    return section


def _evidence(model: ReportModel) -> Section:
    section = Section(
        key="evidence",
        title="Evidence and provenance",
        summary=(
            "Every stored artefact this report cites, with its hash. The report "
            "is auditable because these rows exist: a claim without one of them "
            "is not a claim this document makes."
        ),
        empty_note="No evidence artefacts were stored for this case.",
    )
    for ref in model.evidence:
        section.rows.append(
            {
                "id": ref.id,
                "hash": ref.short_hash,
                "collector": getattr(ref, "collector", None),
                "url": _link(getattr(ref, "source_url", None)),
                "retrieved": _when(getattr(ref, "retrieved_at", None)),
            }
        )
    return section


def _limitations(model: ReportModel) -> Section:
    section = Section(
        key="limitations",
        title="Limitations and unresolved questions",
        summary=("What this report cannot tell you. Read it before acting on " "anything above."),
        empty_note="No limitations were recorded, which is itself worth questioning.",
    )
    for line in model.limitations:
        section.statements.append(Statement(text=line, basis=Basis.UNRESOLVED_LEAD))
    for gap in model.coverage_gaps:
        section.statements.append(
            Statement(text=gap, basis=Basis.UNRESOLVED_LEAD, detail="Coverage gap")
        )
    return section


def _appendix_sources(model: ReportModel) -> Section:
    section = Section(
        key="collector_runs",
        title="Collector runs",
        summary="Every collector this case ran, and what each returned.",
        empty_note="No collector has run for this case.",
    )
    for source in model.sources:
        section.rows.append(
            {
                "collector": source.collector,
                "runs": source.runs,
                "successes": source.successes,
                "failures": source.failures,
                "skipped": source.skipped,
                "findings": source.findings,
                "attribution": source.attribution,
            }
        )
    return section


def _appendix_agreements(model: ReportModel) -> Section:
    section = Section(
        key="source_agreements",
        title="Identifier agreements between sources",
        summary=(
            "Where two indexes carry the same identifier, and whether their "
            "independence could be established. Agreement between sources that "
            "share an upstream is one claim seen twice, not two claims."
        ),
        empty_note="No identifier was published by more than one source.",
    )
    for item in model.source_agreements:
        section.rows.append(
            {
                "identifier": getattr(item, "identifier", ""),
                "value": getattr(item, "value", ""),
                "sources": list(getattr(item, "sources", []) or []),
                "independence": str(getattr(item, "independence", "")),
                "corroborates": bool(getattr(item, "corroborates", False)),
                "reason": getattr(item, "reason", ""),
            }
        )
    return section


def build_dossier(model: ReportModel) -> Dossier:
    """Arrange a report model as the investigator-facing document.

    Pure: takes a model, returns a view of it. No database, no scoring, no
    network, no state. Rendering it twice produces the same document, and
    rendering it at all changes nothing about the case.
    """
    identity, low_confidence = _identity_assessment(model)

    return Dossier(
        model=model,
        profile_image=profile_image_for(model),
        sections=[
            _executive_summary(model),
            _subject_profile(model),
            _key_findings(model),
            identity,
            _profiles(model),
            _contacts(model),
            _affiliations(model),
            _geography(model),
            _relationships(model),
            _timeline(model),
            _decisions(model),
            _coverage(model),
            _evidence(model),
            _limitations(model),
        ],
        appendix=[
            low_confidence,
            _appendix_sources(model),
            _appendix_agreements(model),
        ],
    )
