"""Entity resolution and persistence.

Takes the drafts produced by extraction, reconciles them with what the case
already holds, scores them, and writes entities and relationships.

The resolution policy is deliberately timid:

* entities are keyed on ``(type, canonical_value)``; equal keys are the *same*
  thing and are merged, which is a lookup, not an inference;
* anything softer than that — a username seen on two platforms, similar display
  names — never merges entities. It produces a ``POSSIBLY_SAME_ENTITY`` edge
  with its score and reasons, and a human decides;
* automatic merging of distinct keys happens only at or above
  ``AUTO_MERGE_THRESHOLD`` (0.95), which in practice requires reciprocal
  self-published links.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.correlation.confidence import (
    AUTO_MERGE_THRESHOLD,
    ConfidenceEngine,
    ConfidenceSignal,
    default_engine,
)
from app.correlation.extraction import EntityDraft, ExtractionResult, RelationshipDraft
from app.models.collection import Finding
from app.models.entity import Entity, Relationship
from app.models.enums import EntityType, RelationshipType

log = get_logger(__name__)

#: Minimum length before a shared username is treated as distinctive at all.
MIN_DISTINCTIVE_USERNAME = 4


@dataclass(slots=True)
class ResolutionSummary:
    """What resolution changed, for the job result and the CLI."""

    entities_created: int = 0
    entities_updated: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    inferred_links: int = 0
    merges_suggested: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "entities_created": self.entities_created,
            "entities_updated": self.entities_updated,
            "relationships_created": self.relationships_created,
            "relationships_updated": self.relationships_updated,
            "inferred_links": self.inferred_links,
            "merges_suggested": self.merges_suggested,
        }


class EntityResolver:
    """Persists extraction output and infers cross-source links."""

    def __init__(self, engine: ConfidenceEngine | None = None) -> None:
        self.engine = engine or default_engine

    def resolve(
        self, session: Session, case_id: uuid.UUID, extraction: ExtractionResult
    ) -> ResolutionSummary:
        """Write ``extraction`` into the case and derive inferred links."""
        summary = ResolutionSummary()
        finding_index = self._finding_index(session, case_id, extraction)

        entities: dict[tuple[EntityType, str], Entity] = {}
        for key, draft in extraction.entities.items():
            entity, created = self._upsert_entity(session, case_id, draft, finding_index)
            entities[key] = entity
            if created:
                summary.entities_created += 1
            else:
                summary.entities_updated += 1
        session.flush()

        for edge_draft in extraction.relationships.values():
            source = entities.get(edge_draft.source) or self._lookup(
                session, case_id, edge_draft.source
            )
            target = entities.get(edge_draft.target) or self._lookup(
                session, case_id, edge_draft.target
            )
            if source is None or target is None:
                continue
            _, created = self._upsert_relationship(
                session, case_id, source, target, edge_draft, finding_index
            )
            if created:
                summary.relationships_created += 1
            else:
                summary.relationships_updated += 1
        session.flush()

        inferred = self.infer_links(session, case_id)
        summary.inferred_links = inferred
        summary.merges_suggested = self._count_merge_candidates(session, case_id)
        session.flush()
        return summary

    # ------------------------------------------------------------- entities

    def _finding_index(
        self, session: Session, case_id: uuid.UUID, extraction: ExtractionResult
    ) -> dict[uuid.UUID, Finding]:
        wanted: set[uuid.UUID] = set()
        for draft in extraction.entities.values():
            wanted.update(draft.source_finding_ids)
        for relationship in extraction.relationships.values():
            wanted.update(relationship.evidence_finding_ids)
        if not wanted:
            return {}
        rows = session.scalars(
            select(Finding).where(Finding.case_id == case_id, Finding.id.in_(wanted))
        )
        return {row.id: row for row in rows}

    def _lookup(
        self, session: Session, case_id: uuid.UUID, key: tuple[EntityType, str]
    ) -> Entity | None:
        return session.scalar(
            select(Entity).where(
                Entity.case_id == case_id,
                Entity.type == key[0],
                Entity.canonical_value == key[1],
            )
        )

    def _upsert_entity(
        self,
        session: Session,
        case_id: uuid.UUID,
        draft: EntityDraft,
        finding_index: dict[uuid.UUID, Finding],
    ) -> tuple[Entity, bool]:
        assessment = self.engine.score(draft.signals)
        existing = self._lookup(session, case_id, draft.key)
        sources = [finding_index[fid] for fid in draft.source_finding_ids if fid in finding_index]

        if existing is None:
            entity = Entity(
                case_id=case_id,
                type=draft.type,
                display_name=draft.display_name[:300],
                canonical_value=draft.canonical_value[:1024],
                aliases=sorted(set(draft.aliases)),
                attributes=_clean(draft.attributes),
                confidence=assessment.score,
                confidence_reasons=assessment.reasons,
            )
            entity.sources = sources
            session.add(entity)
            return entity, True

        # Merging an identical key: union the evidence, keep the better score.
        existing.aliases = sorted(set(existing.aliases or []) | set(draft.aliases))
        merged_attributes = {**_clean(draft.attributes), **(existing.attributes or {})}
        existing.attributes = merged_attributes
        if assessment.score > existing.confidence:
            existing.confidence = assessment.score
            existing.confidence_reasons = assessment.reasons
        else:
            existing.confidence_reasons = _merge_reasons(
                existing.confidence_reasons, assessment.reasons
            )
        known = {source.id for source in existing.sources}
        existing.sources.extend(source for source in sources if source.id not in known)
        return existing, False

    # -------------------------------------------------------- relationships

    def _upsert_relationship(
        self,
        session: Session,
        case_id: uuid.UUID,
        source: Entity,
        target: Entity,
        draft: RelationshipDraft,
        finding_index: dict[uuid.UUID, Finding],
    ) -> tuple[Relationship, bool]:
        assessment = self.engine.score(draft.signals)
        existing = session.scalar(
            select(Relationship).where(
                Relationship.case_id == case_id,
                Relationship.source_entity_id == source.id,
                Relationship.target_entity_id == target.id,
                Relationship.type == draft.type,
            )
        )
        evidence = [
            finding_index[fid] for fid in draft.evidence_finding_ids if fid in finding_index
        ]

        if existing is None:
            relationship = Relationship(
                case_id=case_id,
                source_entity_id=source.id,
                target_entity_id=target.id,
                type=draft.type,
                confidence=assessment.score,
                confidence_reasons=assessment.reasons,
                strength=assessment.strength,
                collector=draft.collector,
                source_url=draft.source_url,
                attributes=_clean(draft.attributes),
            )
            relationship.evidence = evidence
            session.add(relationship)
            return relationship, True

        if assessment.score > existing.confidence:
            existing.confidence = assessment.score
            existing.strength = assessment.strength
            existing.confidence_reasons = assessment.reasons
        else:
            existing.confidence_reasons = _merge_reasons(
                existing.confidence_reasons, assessment.reasons
            )
        existing.attributes = {**(existing.attributes or {}), **_clean(draft.attributes)}
        known = {finding.id for finding in existing.evidence}
        existing.evidence.extend(finding for finding in evidence if finding.id not in known)
        return existing, False

    # ------------------------------------------------------------ inference

    def infer_links(self, session: Session, case_id: uuid.UUID) -> int:
        """Derive cross-source links that no single collector could see.

        Two rules only, both explicitly bounded:

        1. accounts on different platforms sharing a distinctive username get a
           ``SAME_USERNAME`` edge — a lead, not an identity claim;
        2. two accounts that each link to the same website, where at least one
           of those links is self-published, get ``POSSIBLY_SAME_ENTITY`` at the
           confidence that combination justifies.
        """
        created = 0
        created += self._infer_shared_usernames(session, case_id)
        created += self._infer_shared_websites(session, case_id)
        return created

    def _infer_shared_usernames(self, session: Session, case_id: uuid.UUID) -> int:
        accounts = list(
            session.scalars(
                select(Entity).where(
                    Entity.case_id == case_id, Entity.type == EntityType.SOCIAL_ACCOUNT
                )
            )
        )
        by_handle: dict[str, list[Entity]] = {}
        for account in accounts:
            handle = str((account.attributes or {}).get("handle", "")).lower()
            if len(handle) < MIN_DISTINCTIVE_USERNAME:
                continue
            by_handle.setdefault(handle, []).append(account)

        created = 0
        for handle, group in by_handle.items():
            if len(group) < 2:
                continue
            signal_key = "same_unique_username" if len(handle) >= 6 else "same_common_username"
            for index, first in enumerate(group):
                for second in group[index + 1 :]:
                    if _same_platform(first, second):
                        continue
                    if self._link(
                        session,
                        case_id,
                        first,
                        second,
                        RelationshipType.SAME_USERNAME,
                        [self.engine.signal(signal_key, f"handle {handle!r}")],
                        attributes={"handle": handle, "inferred": True},
                    ):
                        created += 1
        return created

    def _infer_shared_websites(self, session: Session, case_id: uuid.UUID) -> int:
        links = list(
            session.scalars(
                select(Relationship).where(
                    Relationship.case_id == case_id,
                    Relationship.type == RelationshipType.LINKS_TO,
                )
            )
        )
        # A self-published link counts in either direction: the site may name
        # the profile, or the profile may name the site.
        by_site: dict[uuid.UUID, list[Entity]] = {}
        for link in links:
            pair = _account_and_website(link)
            if pair is None:
                continue
            account, website_id = pair
            accounts = by_site.setdefault(website_id, [])
            if all(account.id != known.id for known in accounts):
                accounts.append(account)

        created = 0
        for website_id, group in by_site.items():
            if len(group) < 2:
                continue
            for index, left in enumerate(group):
                for right in group[index + 1 :]:
                    if left.id == right.id:
                        continue
                    signals = [
                        self.engine.signal(
                            (
                                "same_username_shared_website"
                                if _same_handle(left, right)
                                else "shared_infrastructure"
                            ),
                            "both accounts link to the same website",
                        )
                    ]
                    if self._link(
                        session,
                        case_id,
                        left,
                        right,
                        RelationshipType.POSSIBLY_SAME_ENTITY,
                        signals,
                        attributes={"inferred": True, "shared_website": str(website_id)},
                    ):
                        created += 1
        return created

    def _link(
        self,
        session: Session,
        case_id: uuid.UUID,
        source: Entity,
        target: Entity,
        relationship_type: RelationshipType,
        signals: list[ConfidenceSignal],
        *,
        attributes: dict | None = None,
    ) -> bool:
        """Create an inferred edge if it does not already exist."""
        exists = session.scalar(
            select(Relationship).where(
                Relationship.case_id == case_id,
                Relationship.source_entity_id.in_([source.id, target.id]),
                Relationship.target_entity_id.in_([source.id, target.id]),
                Relationship.type == relationship_type,
            )
        )
        if exists is not None:
            return False
        assessment = self.engine.score(signals)
        session.add(
            Relationship(
                case_id=case_id,
                source_entity_id=source.id,
                target_entity_id=target.id,
                type=relationship_type,
                confidence=assessment.score,
                confidence_reasons=assessment.reasons,
                strength=assessment.strength,
                collector="correlation",
                attributes=attributes or {},
            )
        )
        return True

    def _count_merge_candidates(self, session: Session, case_id: uuid.UUID) -> int:
        """Edges strong enough that a human should consider merging the pair.

        The platform reports these; it does not act on them. Nothing in this
        codebase merges two entities with different canonical values on its own.
        """
        candidates = session.scalars(
            select(Relationship).where(
                Relationship.case_id == case_id,
                Relationship.type == RelationshipType.POSSIBLY_SAME_ENTITY,
                Relationship.confidence >= AUTO_MERGE_THRESHOLD,
            )
        )
        return len(list(candidates))


def _account_and_website(link: Relationship) -> tuple[Entity, uuid.UUID] | None:
    """Return ``(account, website_id)`` when a LINKS_TO edge joins the two."""
    source, target = link.source_entity, link.target_entity
    if source.type is EntityType.SOCIAL_ACCOUNT and target.type is EntityType.WEBSITE:
        return source, link.target_entity_id
    if source.type is EntityType.WEBSITE and target.type is EntityType.SOCIAL_ACCOUNT:
        return target, link.source_entity_id
    return None


def _same_platform(first: Entity, second: Entity) -> bool:
    return (first.attributes or {}).get("platform") == (second.attributes or {}).get("platform")


def _same_handle(first: Entity, second: Entity) -> bool:
    left = str((first.attributes or {}).get("handle", "")).lower()
    right = str((second.attributes or {}).get("handle", "")).lower()
    return bool(left) and left == right


def _merge_reasons(existing: list | None, new: list[str]) -> list[str]:
    merged = list(existing or [])
    for reason in new:
        if reason not in merged:
            merged.append(reason)
    return merged[:20]


def _clean(attributes: dict) -> dict:
    """Drop null values so stored attributes stay compact and meaningful."""
    return {key: value for key, value in attributes.items() if value is not None}
