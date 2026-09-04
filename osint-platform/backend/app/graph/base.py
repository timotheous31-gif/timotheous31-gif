"""Graph backend abstraction.

Development runs on NetworkX because it needs no server. The interface below is
what a Neo4j (or other property-graph) backend would implement: node and edge
upserts, neighbourhood queries, path finding and export. Nothing outside
``app/graph`` imports NetworkX, so swapping the backend touches one module.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import EntityType, RelationshipType


@dataclass(slots=True)
class GraphNode:
    """A node in the relationship graph."""

    id: str
    type: EntityType
    label: str
    confidence: float = 0.5
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": str(self.type),
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class GraphEdge:
    """A typed, evidence-backed edge."""

    id: str
    source: str
    target: str
    type: RelationshipType
    confidence: float = 0.5
    strength: str = "WEAK_ASSOCIATION"
    reasons: list[str] = field(default_factory=list)
    collector: str = "correlation"
    evidence_ids: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": str(self.type),
            "confidence": round(self.confidence, 4),
            "strength": self.strength,
            "reasons": self.reasons,
            "collector": self.collector,
            "evidence_ids": self.evidence_ids,
            "attributes": self.attributes,
        }


class GraphBackend(abc.ABC):
    """Operations every graph backend must provide."""

    @abc.abstractmethod
    def add_node(self, node: GraphNode) -> None: ...

    @abc.abstractmethod
    def add_edge(self, edge: GraphEdge) -> None: ...

    @abc.abstractmethod
    def nodes(self) -> list[GraphNode]: ...

    @abc.abstractmethod
    def edges(self) -> list[GraphEdge]: ...

    @abc.abstractmethod
    def neighbors(self, node_id: str, *, depth: int = 1) -> list[GraphNode]: ...

    @abc.abstractmethod
    def subgraph(
        self,
        *,
        min_confidence: float = 0.0,
        entity_types: list[EntityType] | None = None,
        relationship_types: list[RelationshipType] | None = None,
    ) -> GraphBackend: ...

    @abc.abstractmethod
    def shortest_path(self, source_id: str, target_id: str) -> list[str]: ...

    @abc.abstractmethod
    def degree(self, node_id: str) -> int: ...

    @abc.abstractmethod
    def components(self) -> list[list[str]]: ...

    def to_dict(self) -> dict[str, Any]:
        """Cytoscape/React-Flow friendly export."""
        nodes = self.nodes()
        edges = self.edges()
        return {
            "nodes": [node.to_dict() for node in nodes],
            "edges": [edge.to_dict() for edge in edges],
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "entity_types": sorted({str(node.type) for node in nodes}),
                "relationship_types": sorted({str(edge.type) for edge in edges}),
            },
        }
