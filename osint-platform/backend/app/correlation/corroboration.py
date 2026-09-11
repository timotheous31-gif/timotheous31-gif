"""Cross-source corroboration for PERSON candidates, and its limits.

Two independently operated sources publishing the same identifier for the same
record is real evidence: neither could have copied it from the other, so the
agreement means something. Two *views of the same upstream data* agreeing means
nothing at all, and counting it would inflate confidence exactly where an
investigator is least able to notice.

Deciding which of those two a given agreement is belongs to
:mod:`app.correlation.lineage`, which separates the API that answered from where
the claim originated, and rules ``INDEPENDENT``, ``DEPENDENT`` or ``UNKNOWN``.
This module applies that ruling:

* ``INDEPENDENT`` fires the named ``independent_corroboration`` rule;
* ``DEPENDENT`` and ``UNKNOWN`` fire nothing, and are recorded on the entity as
  shared identifiers with the reason they do not count.

The agreement is never discarded — an investigator wants to know two indexes
carry the same ORCID iD — but the report says which of the two claims it is
making, and a score moves only for the first.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.correlation.confidence import ConfidenceEngine, default_engine
from app.correlation.lineage import (
    STATUS_EFFECT,
    STATUS_LABELS,
    Independence,
    Verdict,
    independence,
)
from app.correlation.sources import explain_source
from app.models import Entity, Relationship
from app.models.enums import EntityType, MatchStrength, RelationshipType

#: Kept for callers and tests that ask the coarse question "could these two ever
#: corroborate each other at all?". The authority is :mod:`app.correlation.lineage`;
#: this is a derived convenience, not a second source of truth.
PROVENANCE_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"openalex", "crossref"}),
    frozenset({"github", "github_people"}),
    frozenset({"wikidata", "orcid"}),
    frozenset({"openalex", "orcid"}),
)

#: Identifier kinds strong enough that agreement across sources is meaningful.
#: A shared *name* is deliberately absent: that is the thing being tested.
CORROBORATING_IDENTIFIERS: frozenset[str] = frozenset(
    {"orcid", "doi", "github_login", "wikidata", "openalex"}
)


@dataclass(slots=True)
class Corroboration:
    """One identifier two sources both published, and what that is worth.

    ``status`` is the lineage ruling. Only ``INDEPENDENT`` changes a score; the
    other two are carried so the report can show the agreement and say, in the
    same breath, that it is not corroboration.
    """

    identifier: str
    value: str
    sources: list[str] = field(default_factory=list)
    entity_ids: list[uuid.UUID] = field(default_factory=list)
    status: Independence = Independence.UNKNOWN
    lineage_reason: str = ""

    @property
    def amplifies(self) -> bool:
        return self.status is Independence.INDEPENDENT

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def reason(self) -> str:
        listed = " and ".join(sorted(self.sources))
        if self.amplifies:
            return (
                f"{listed} independently publish {self.identifier.upper()} {self.value} "
                f"for this record, and neither takes that value from the other."
            )
        return (
            f"{listed} both carry {self.identifier.upper()} {self.value} for this record. "
            f"{self.lineage_reason} {STATUS_EFFECT[self.status]}"
        ).strip()


@dataclass(slots=True)
class CorroborationSummary:
    """What a corroboration pass found and did."""

    corroborations: list[Corroboration] = field(default_factory=list)
    #: Agreements kept for the reader but scored at nothing: dependent lineage,
    #: or lineage that could not be established.
    shared_identifiers: list[Corroboration] = field(default_factory=list)
    entities_strengthened: int = 0
    duplicate_pairs_ignored: int = 0

    def _describe(self, item: Corroboration) -> dict[str, object]:
        return {
            "identifier": item.identifier,
            "value": item.value,
            "sources": sorted(item.sources),
            "independence": str(item.status),
            "label": item.label,
            "reason": item.reason,
            "scoring_effect": STATUS_EFFECT[item.status],
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "corroborations": [self._describe(item) for item in self.corroborations],
            "shared_identifiers": [self._describe(item) for item in self.shared_identifiers],
            "entities_strengthened": self.entities_strengthened,
            "duplicate_pairs_ignored": self.duplicate_pairs_ignored,
        }


def are_independent(first: str, second: str, identifier: str = "") -> bool:
    """True only when independence is *established* for this identifier.

    Unknown lineage answers False. That is the whole point: a different hostname
    is not evidence of a different party, and the previous version of this
    function returned True for every pair it had not been told about — which is
    how ORCID and Wikidata came to corroborate each other on an iD Wikidata bots
    import out of ORCID.
    """
    if not identifier:
        # Without an identifier kind there is nothing to reason about, so the
        # only safe answer is the conservative one.
        return first != second and not any(
            {first, second} <= family for family in PROVENANCE_FAMILIES
        )
    return independence(first, second, identifier).amplifies


def rule_on(
    first: str,
    second: str,
    identifier: str,
    *,
    first_claim: dict[str, object] | None = None,
    second_claim: dict[str, object] | None = None,
) -> Verdict:
    """The full independence ruling, with the sentence that explains it."""
    return independence(
        first, second, identifier, first_claim=first_claim, second_claim=second_claim
    )


class CorroborationService:
    """Strengthens candidates that independent sources agree about."""

    def __init__(self, engine: ConfidenceEngine | None = None) -> None:
        self.engine = engine or default_engine

    def run(self, session: Session, case_id: uuid.UUID) -> CorroborationSummary:
        """Find identifiers two independent sources agree on, and record them.

        Confidence is raised through the ordinary named rule, so the ceiling
        still applies and a corroborated candidate remains a candidate: this
        service can strengthen a case for review, never close it.
        """
        candidates = [
            entity
            for entity in session.scalars(
                select(Entity).where(Entity.case_id == case_id, Entity.type == EntityType.PERSONA)
            )
            if str(entity.canonical_value).startswith("person-candidate:")
        ]
        summary = CorroborationSummary()
        if len(candidates) < 2:
            return summary

        # identifier kind -> value -> [(source, entity, that claim's own provenance)]
        index: dict[str, dict[str, list[tuple[str, Entity, object]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for entity in candidates:
            attributes = entity.attributes or {}
            source = str(attributes.get("source") or "")
            identifiers = attributes.get("identifiers") or {}
            if not isinstance(identifiers, dict):
                continue
            claims = attributes.get("claim_lineage")
            claims = claims if isinstance(claims, dict) else {}
            for kind, value in identifiers.items():
                if kind in CORROBORATING_IDENTIFIERS and str(value).strip():
                    index[kind][str(value).strip().lower()].append(
                        (source, entity, claims.get(kind))
                    )

        strengthened: set[uuid.UUID] = set()
        for kind, by_value in index.items():
            for value, holders in by_value.items():
                if len(holders) < 2:
                    continue
                verdict, sources = self._rule(kind, holders)
                agreement = Corroboration(
                    identifier=kind,
                    value=value,
                    sources=sources,
                    entity_ids=[entity.id for _, entity, _ in holders],
                    status=verdict.status,
                    lineage_reason=verdict.reason,
                )
                if not agreement.amplifies:
                    # Kept, shown, and scored at nothing. An agreement whose
                    # independence is unknown is a lead, not corroboration.
                    summary.shared_identifiers.append(agreement)
                    summary.duplicate_pairs_ignored += 1
                    for _, entity, _ in holders:
                        self._record_shared(session, entity, agreement)
                    continue

                summary.corroborations.append(agreement)
                for _, entity, _ in holders:
                    if self._strengthen(session, entity, agreement):
                        strengthened.add(entity.id)

        summary.entities_strengthened = len(strengthened)
        session.flush()
        return summary

    def _rule(
        self, identifier: str, holders: list[tuple[str, Entity, object]]
    ) -> tuple[Verdict, list[str]]:
        """The best ruling available across every pair of sources here.

        "Best" means the strongest status any *pair* achieves: one established
        independent pair is corroboration even if a third index also carries the
        value. With no independent pair, the ruling reported is the one that
        explains why — a stated dependency in preference to a bare unknown.
        """
        sources = sorted({source for source, _, _ in holders if source})
        by_source = {source: claim for source, _, claim in holders}
        if len(sources) < 2:
            only = sources[0] if sources else "an unnamed source"
            return (
                Verdict(
                    Independence.DEPENDENT,
                    f"Every record carrying this value came from {only}, so this is one "
                    f"source repeating itself.",
                ),
                sources,
            )

        best: Verdict | None = None
        best_pair: tuple[str, str] | None = None
        for index, first in enumerate(sources):
            for second in sources[index + 1 :]:
                verdict = independence(
                    first,
                    second,
                    identifier,
                    first_claim=_claim(by_source.get(first)),
                    second_claim=_claim(by_source.get(second)),
                )
                if verdict.amplifies:
                    return verdict, [first, second]
                if best is None or (
                    best.status is Independence.UNKNOWN and verdict.status is Independence.DEPENDENT
                ):
                    best, best_pair = verdict, (first, second)
        assert best is not None and best_pair is not None
        return best, list(best_pair)

    def _record_shared(self, session: Session, entity: Entity, agreement: Corroboration) -> None:
        """Note an agreement that does not corroborate, and change no score.

        Deliberately writes to a different key from ``corroborating_sources``:
        a reader (and the report) must never have to guess which kind of claim a
        stored list is making.
        """
        attributes = dict(entity.attributes or {})
        shared = [
            item for item in (attributes.get("shared_identifiers") or []) if isinstance(item, dict)
        ]
        entry = {
            "identifier": agreement.identifier,
            "value": agreement.value,
            "sources": sorted(agreement.sources),
            "independence": str(agreement.status),
            "label": agreement.label,
            "reason": agreement.reason,
        }
        if entry in shared:
            return
        shared.append(entry)
        attributes["shared_identifiers"] = shared
        entity.attributes = attributes
        session.flush()

    def _strengthen(self, session: Session, entity: Entity, corroboration: Corroboration) -> bool:
        """Record the corroboration on an entity and on its subject link."""
        attributes = dict(entity.attributes or {})
        existing = list(attributes.get("corroborating_sources") or [])
        if corroboration.reason in list(attributes.get("corroboration_reasons") or []):
            return False

        attributes["corroborating_sources"] = sorted(set(existing) | set(corroboration.sources))
        attributes["corroboration_reasons"] = [
            *(attributes.get("corroboration_reasons") or []),
            corroboration.reason,
        ]
        attributes.setdefault("source_explanations", {})
        for source in corroboration.sources:
            attributes["source_explanations"][source] = explain_source(source)
        entity.attributes = attributes

        signal = self.engine.signal("independent_corroboration", detail=corroboration.value)
        reasons = [*(entity.confidence_reasons or []), signal.reason]
        assessment = self.engine.score(
            [
                *(self.engine.rules[key].signal() for key in _signal_keys(entity)),
                signal,
            ]
        )
        entity.confidence = assessment.score
        entity.confidence_reasons = reasons

        for link in session.scalars(
            select(Relationship).where(
                Relationship.case_id == entity.case_id,
                Relationship.target_entity_id == entity.id,
                Relationship.type == RelationshipType.POSSIBLY_SAME_ENTITY,
            )
        ):
            link_attributes = dict(link.attributes or {})
            link_attributes["corroborating_sources"] = corroboration.sources
            link.attributes = link_attributes
            link.confidence = max(link.confidence, assessment.score)
            link.strength = MatchStrength(assessment.strength)
            link.confidence_reasons = [*(link.confidence_reasons or []), corroboration.reason]
        return True


def _claim(value: object) -> dict[str, object] | None:
    """Per-claim provenance, when a source published any."""
    return value if isinstance(value, dict) else None


def _signal_keys(entity: Entity) -> list[str]:
    """The rules already recorded on an entity, so rescoring keeps them."""
    stored = (entity.attributes or {}).get("signal_keys")
    if isinstance(stored, list):
        return [str(key) for key in stored if key in default_engine.rules]
    # Fall back to the one rule every candidate carries.
    return ["same_person_name"]
