"""Graph backend and builder."""

from __future__ import annotations

import pytest

from app.graph import NetworkXBackend, graph_summary
from app.graph.base import GraphEdge, GraphNode
from app.models.enums import EntityType, RelationshipType


def node(node_id: str, entity_type=EntityType.DOMAIN, confidence=0.9) -> GraphNode:
    return GraphNode(id=node_id, type=entity_type, label=node_id, confidence=confidence)


def edge(edge_id, source, target, confidence=0.9, edge_type=RelationshipType.RESOLVES_TO):
    return GraphEdge(
        id=edge_id, source=source, target=target, type=edge_type, confidence=confidence
    )


@pytest.fixture
def graph():
    backend = NetworkXBackend()
    backend.add_node(node("a"))
    backend.add_node(node("b", EntityType.IP_ADDRESS))
    backend.add_node(node("c", EntityType.SOCIAL_ACCOUNT, confidence=0.4))
    backend.add_node(node("isolated", EntityType.EMAIL))
    backend.add_edge(edge("e1", "a", "b", 0.95))
    backend.add_edge(edge("e2", "b", "c", 0.3, RelationshipType.SAME_USERNAME))
    return backend


def test_nodes_and_edges_round_trip(graph):
    assert {n.id for n in graph.nodes()} == {"a", "b", "c", "isolated"}
    assert {e.id for e in graph.edges()} == {"e1", "e2"}


def test_edge_to_a_missing_node_is_rejected(graph):
    with pytest.raises(KeyError, match="not in the graph"):
        graph.add_edge(edge("bad", "a", "nowhere"))


def test_parallel_edges_of_different_types_are_kept(graph):
    graph.add_edge(edge("e3", "a", "b", 0.5, RelationshipType.LINKS_TO))
    assert len(graph.edges()) == 3


def test_neighbors_respect_depth(graph):
    assert {n.id for n in graph.neighbors("a", depth=1)} == {"b"}
    assert {n.id for n in graph.neighbors("a", depth=2)} == {"b", "c"}
    assert graph.neighbors("missing") == []


def test_confidence_filter_drops_weak_edges(graph):
    filtered = graph.subgraph(min_confidence=0.9)
    assert {e.id for e in filtered.edges()} == {"e1"}
    # Filtering edges must not silently delete the nodes they connected.
    assert {n.id for n in filtered.nodes()} == {"a", "b", "c", "isolated"}


def test_entity_type_filter(graph):
    filtered = graph.subgraph(entity_types=[EntityType.DOMAIN, EntityType.IP_ADDRESS])
    assert {n.id for n in filtered.nodes()} == {"a", "b"}
    assert {e.id for e in filtered.edges()} == {"e1"}


def test_relationship_type_filter(graph):
    filtered = graph.subgraph(relationship_types=[RelationshipType.SAME_USERNAME])
    assert {e.id for e in filtered.edges()} == {"e2"}


def test_shortest_path(graph):
    assert graph.shortest_path("a", "c") == ["a", "b", "c"]
    assert graph.shortest_path("a", "isolated") == []
    assert graph.shortest_path("a", "missing") == []


def test_degree_and_components(graph):
    assert graph.degree("b") == 2
    assert graph.degree("isolated") == 0
    assert graph.degree("missing") == 0
    components = graph.components()
    assert components[0] == ["a", "b", "c"]
    assert ["isolated"] in components


def test_most_connected_ranks_pivots(graph):
    ranked = graph.most_connected(2)
    assert ranked[0][0].id == "b"
    assert ranked[0][1] == 2


def test_export_shape_is_frontend_ready(graph):
    exported = graph.to_dict()
    assert set(exported) == {"nodes", "edges", "stats"}
    assert exported["stats"]["node_count"] == 4
    assert exported["stats"]["edge_count"] == 2
    first = exported["nodes"][0]
    assert set(first) == {"id", "type", "label", "confidence", "attributes"}
    assert isinstance(exported["edges"][0]["type"], str)


def test_summary_counts_by_type(graph):
    summary = graph_summary(graph)
    assert summary["node_count"] == 4
    assert summary["entity_type_counts"]["DOMAIN"] == 1
    assert summary["relationship_type_counts"]["RESOLVES_TO"] == 1
    assert summary["component_count"] == 2
    assert summary["largest_component_size"] == 3
    assert summary["most_connected"][0]["id"] == "b"


def test_empty_graph_summary():
    summary = graph_summary(NetworkXBackend())
    assert summary["node_count"] == 0
    assert summary["largest_component_size"] == 0
    assert summary["most_connected"] == []


def test_backend_interface_is_abstract():
    from app.graph.base import GraphBackend

    with pytest.raises(TypeError):
        GraphBackend()  # type: ignore[abstract]
