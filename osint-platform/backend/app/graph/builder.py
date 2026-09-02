"""Builds a :class:`GraphBackend` from a case's persisted entities and edges."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from app.graph.base import GraphBackend, GraphEdge, GraphNode
from app.graph.networkx_backend import NetworkXBackend
from app.models.entity import Entity, Relationship


def build_graph(
    entities: Iterable[Entity],
    relationships: Iterable[Relationship],
    *,
    backend: GraphBackend | None = None,
) -> GraphBackend:
    """Assemble a graph from ORM rows.

    Edges whose endpoints are missing are skipped rather than raising: a
    partially loaded case (a filtered query, for instance) should still render.
    """
    graph = backend or NetworkXBackend()
    known: set[str] = set()

    for entity in entities:
        node_id = str(entity.id)
        known.add(node_id)
        graph.add_node(
            GraphNode(
                id=node_id,
                type=entity.type,
                label=entity.display_name or entity.canonical_value,
                confidence=entity.confidence,
                attributes={
                    "canonical_value": entity.canonical_value,
                    "aliases": list(entity.aliases or []),
                    "source_count": len(entity.sources or []),
                    **(entity.attributes or {}),
                },
            )
        )

    for relationship in relationships:
        source = str(relationship.source_entity_id)
        target = str(relationship.target_entity_id)
        if source not in known or target not in known:
            continue
        graph.add_edge(
            GraphEdge(
                id=str(relationship.id),
                source=source,
                target=target,
                type=relationship.type,
                confidence=relationship.confidence,
                strength=str(relationship.strength),
                reasons=list(relationship.confidence_reasons or []),
                collector=relationship.collector,
                evidence_ids=[str(finding.id) for finding in (relationship.evidence or [])],
                attributes=dict(relationship.attributes or {}),
            )
        )
    return graph


def graph_summary(graph: GraphBackend) -> dict[str, Any]:
    """Counts and pivots for the dashboard's graph panel."""
    nodes = graph.nodes()
    edges = graph.edges()
    by_type: dict[str, int] = {}
    for node in nodes:
        by_type[str(node.type)] = by_type.get(str(node.type), 0) + 1
    by_relationship: dict[str, int] = {}
    for edge in edges:
        by_relationship[str(edge.type)] = by_relationship.get(str(edge.type), 0) + 1

    components = graph.components()
    pivots: list[dict[str, Any]] = []
    if isinstance(graph, NetworkXBackend):
        pivots = [
            {"id": node.id, "label": node.label, "type": str(node.type), "degree": degree}
            for node, degree in graph.most_connected(5)
            if degree > 0
        ]

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "entity_type_counts": dict(sorted(by_type.items())),
        "relationship_type_counts": dict(sorted(by_relationship.items())),
        "component_count": len(components),
        "largest_component_size": len(components[0]) if components else 0,
        "most_connected": pivots,
    }


def node_id_for(entity_id: uuid.UUID | str) -> str:
    return str(entity_id)
