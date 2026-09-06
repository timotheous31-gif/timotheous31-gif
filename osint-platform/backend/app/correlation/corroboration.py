"""Cross-source corroboration for PERSON candidates.

Two independently operated sources publishing the same identifier for the same
record is real evidence: neither could have copied it from the other, so the
agreement means something. Two *views of the same upstream data* agreeing means
nothing at all, and counting it would inflate confidence exactly where an
investigator is least able to notice.

So this module does two things:

1. group candidates by the identifiers they carry, and
2. refuse to treat sources in the same provenance family as independent.

OpenAlex ingests Crossref. An OpenAlex author record and a Crossref work
agreeing on a DOI is one fact reported twice, and the deduplication here says so
rather than scoring it twice.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.correlation.confidence import ConfidenceEngine, default_engine
from app.correlation.sources import explain_source
from app.models import Entity, Relationship
from app.models.enums import EntityType, MatchStrength, RelationshipType

#: Sources that share upstream data. Members of one family never corroborate
#: each other, however many identifiers they agree on.
PROVENANCE_FAMILIES: tuple[frozenset[str], ...] = (
    # OpenAlex indexes Crossref's metadata, so a DOI appearing in both is one
    # deposit seen twice.
    frozenset({"openalex", "crossref"}),
    # Both read the same GitHub API.
    frozenset({"github", "github_people"}),
)

#: Identifier kinds strong enough that agreement across sources is meaningful.
#: A shared *name* is deliberately absent: that is the thing being tested.
CORROBORATING_IDENTIFIERS: frozenset[str] = frozenset(
    {"orcid", "doi", "github_login", "wikidata", "openalex"}
)


@dataclass(slots=True)
class Corroboration:
    """One identifier that two independent sources both published."""

    identifier: str
    value: str
    sources: list[str] = field(default_factory=list)
    entity_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def reason(self) -> str:
        listed = " and ".join(sorted(self.sources))
        return (
            f"{listed} independently publish {self.identifier.upper()} {self.value} "
            f"for this record, and neither takes that value from the other."
        )


@dataclass(slots=True)
class CorroborationSummary:
    """What a corroboration pass found and did."""

    corroborations: list[Corroboration] = field(default_factory=list)
    entities_strengthened: int = 0
    duplicate_pairs_ignored: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "corroborations": [
                {
                    "identifier": item.identifier,
                    "value": item.value,
                    "sources": sorted(item.sources),
                    "reason": item.reason,
                }
                for item in self.corroborations
            ],
            "entities_strengthened": self.entities_strengthened,
            "duplicate_pairs_ignored": self.duplicate_pairs_ignored,
        }


def are_independent(first: str, second: str) -> bool:
    """False when two sources share upstream data, or are the same source."""
    if first == second:
        return False
    return not any({first, second} <= family for family in PROVENANCE_FAMILIES)


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

        # identifier kind -> value -> [(source, entity)]
        index: dict[str, dict[str, list[tuple[str, Entity]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for entity in candidates:
            attributes = entity.attributes or {}
            source = str(attributes.get("source") or "")
            identifiers = attributes.get("identifiers") or {}
            if not isinstance(identifiers, dict):
                continue
            for kind, value in identifiers.items():
                if kind in CORROBORATING_IDENTIFIERS and str(value).strip():
                    index[kind][str(value).strip().lower()].append((source, entity))

        strengthened: set[uuid.UUID] = set()
        for kind, by_value in index.items():
            for value, holders in by_value.items():
                if len(holders) < 2:
                    continue
                independent = self._independent_sources(holders)
                if len(independent) < 2:
                    summary.duplicate_pairs_ignored += 1
                    continue

                corroboration = Corroboration(
                    identifier=kind,
                    value=value,
                    sources=sorted(independent),
                    entity_ids=[entity.id for _, entity in holders],
                )
                summary.corroborations.append(corroboration)
                for _, entity in holders:
                    if self._strengthen(session, entity, corroboration):
                        strengthened.add(entity.id)

        summary.entities_strengthened = len(strengthened)
        session.flush()
        return summary

    def _independent_sources(self, holders: list[tuple[str, Entity]]) -> set[str]:
        """The largest set of sources here that are pairwise independent."""
        sources = {source for source, _ in holders if source}
        independent: set[str] = set()
        for source in sorted(sources):
            if all(are_independent(source, other) for other in independent):
                independent.add(source)
        return independent

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


def _signal_keys(entity: Entity) -> list[str]:
    """The rules already recorded on an entity, so rescoring keeps them."""
    stored = (entity.attributes or {}).get("signal_keys")
    if isinstance(stored, list):
        return [str(key) for key in stored if key in default_engine.rules]
    # Fall back to the one rule every candidate carries.
    return ["same_person_name"]
