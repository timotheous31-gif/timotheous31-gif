"""NetworkX implementation of :class:`GraphBackend`."""

from __future__ import annotations

import networkx as nx

from app.graph.base import GraphBackend, GraphEdge, GraphNode
from app.models.enums import EntityType, RelationshipType


class NetworkXBackend(GraphBackend):
    """In-memory multi-digraph.

    A ``MultiDiGraph`` is used because two entities can legitimately be joined
    by several differently-typed edges (``LINKS_TO`` and ``SAME_USERNAME``, for
    example), and collapsing those would lose evidence.
    """

    def __init__(self, graph: nx.MultiDiGraph | None = None) -> None:
        self._graph = graph if graph is not None else nx.MultiDiGraph()

    @property
    def raw(self) -> nx.MultiDiGraph:
        """The underlying NetworkX graph (for algorithms not exposed here)."""
        return self._graph

    def add_node(self, node: GraphNode) -> None:
        self._graph.add_node(node.id, payload=node)

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source not in self._graph or edge.target not in self._graph:
            raise KeyError(
                f"Edge {edge.id} references a node that is not in the graph "
                f"({edge.source} -> {edge.target})"
            )
        self._graph.add_edge(edge.source, edge.target, key=edge.id, payload=edge)

    def nodes(self) -> list[GraphNode]:
        return [data["payload"] for _, data in self._graph.nodes(data=True) if "payload" in data]

    def edges(self) -> list[GraphEdge]:
        return [data["payload"] for _, _, data in self._graph.edges(data=True) if "payload" in data]

    def neighbors(self, node_id: str, *, depth: int = 1) -> list[GraphNode]:
        """Nodes within ``depth`` hops, ignoring edge direction."""
        if node_id not in self._graph:
            return []
        undirected = self._graph.to_undirected(as_view=True)
        reachable = nx.single_source_shortest_path_length(undirected, node_id, cutoff=max(1, depth))
        return [
            self._graph.nodes[found]["payload"]
            for found in reachable
            if found != node_id and "payload" in self._graph.nodes[found]
        ]

    def subgraph(
        self,
        *,
        min_confidence: float = 0.0,
        entity_types: list[EntityType] | None = None,
        relationship_types: list[RelationshipType] | None = None,
    ) -> NetworkXBackend:
        """A filtered copy. Nodes left with no edges are kept only if they pass
        the node filters themselves — filtering must never invent isolation."""
        allowed_entities = set(entity_types or [])
        allowed_relationships = set(relationship_types or [])

        filtered = NetworkXBackend()
        kept_nodes = {
            node.id: node
            for node in self.nodes()
            if (not allowed_entities or node.type in allowed_entities)
        }
        for node in kept_nodes.values():
            filtered.add_node(node)
        for edge in self.edges():
            if edge.confidence < min_confidence:
                continue
            if allowed_relationships and edge.type not in allowed_relationships:
                continue
            if edge.source not in kept_nodes or edge.target not in kept_nodes:
                continue
            filtered.add_edge(edge)
        return filtered

    def shortest_path(self, source_id: str, target_id: str) -> list[str]:
        """Shortest undirected path, or an empty list when none exists."""
        if source_id not in self._graph or target_id not in self._graph:
            return []
        try:
            return list(
                nx.shortest_path(self._graph.to_undirected(as_view=True), source_id, target_id)
            )
        except nx.NetworkXNoPath:
            return []

    def degree(self, node_id: str) -> int:
        if node_id not in self._graph:
            return 0
        return int(self._graph.degree(node_id))

    def components(self) -> list[list[str]]:
        """Connected components, largest first."""
        undirected = self._graph.to_undirected(as_view=True)
        return sorted(
            (sorted(component) for component in nx.connected_components(undirected)),
            key=len,
            reverse=True,
        )

    def most_connected(self, limit: int = 10) -> list[tuple[GraphNode, int]]:
        """The best-connected nodes — usually the pivots of an investigation."""
        ranked = sorted(
            ((node, self.degree(node.id)) for node in self.nodes()),
            key=lambda item: (-item[1], item[0].label),
        )
        return ranked[:limit]
