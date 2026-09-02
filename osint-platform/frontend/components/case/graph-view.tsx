"use client";

/**
 * Interactive relationship graph.
 *
 * Cytoscape is loaded on the client only. Two design rules carry over from the
 * rest of the platform: entity type is conveyed by shape *and* label as well as
 * colour, and edge width follows confidence so weak associations read as weak
 * even in a screenshot.
 */

import cytoscape, { type Core, type EdgeSingular, type ElementDefinition } from "cytoscape";
import type { Css } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button, Card, Empty, Mono } from "@/components/ui/primitives";
import { confidenceBand, formatConfidence, humanise } from "@/lib/format";
import type { GraphEdge, GraphNode, GraphResponse } from "@/types/api";

/** Shape per entity type, so the graph is readable without colour. */
type NodeShape = Css.NodeShape;

const SHAPES: Record<string, NodeShape> = {
  DOMAIN: "round-rectangle",
  WEBSITE: "round-rectangle",
  IP_ADDRESS: "diamond",
  SOCIAL_ACCOUNT: "ellipse",
  USERNAME: "ellipse",
  EMAIL: "hexagon",
  ORGANIZATION: "octagon",
  REPOSITORY: "round-tag",
  CERTIFICATE: "triangle",
  DOCUMENT: "round-diamond",
  PERSONA: "star",
};

const COLOURS: Record<string, string> = {
  DOMAIN: "#3b82f6",
  WEBSITE: "#0ea5e9",
  IP_ADDRESS: "#8b5cf6",
  SOCIAL_ACCOUNT: "#10b981",
  USERNAME: "#22c55e",
  EMAIL: "#f59e0b",
  ORGANIZATION: "#ef4444",
  REPOSITORY: "#14b8a6",
  CERTIFICATE: "#a855f7",
  DOCUMENT: "#64748b",
  PERSONA: "#ec4899",
};

export function GraphView({ graph }: { graph: GraphResponse }) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<Core | null>(null);
  const [selected, setSelected] = useState<GraphNode | GraphEdge | null>(null);
  const [selectionKind, setSelectionKind] = useState<"node" | "edge" | null>(null);

  const elements = useMemo<ElementDefinition[]>(() => {
    const nodes = graph.nodes.map((node) => ({
      data: {
        id: node.id,
        label: node.label,
        type: node.type,
        confidence: node.confidence,
        raw: node,
      },
    }));
    const edges = graph.edges.map((edge) => ({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.type.replace(/_/g, " ").toLowerCase(),
        confidence: edge.confidence,
        raw: edge,
      },
    }));
    return [...nodes, ...edges];
  }, [graph]);

  useEffect(() => {
    if (!container.current) return;

    const core = cytoscape({
      container: container.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (element) =>
              COLOURS[element.data("type") as string] ?? "#64748b",
            shape: (element): NodeShape =>
              SHAPES[element.data("type") as string] ?? "ellipse",
            label: "data(label)",
            "font-size": "10px",
            color: "#8a8f98",
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-wrap": "ellipsis",
            "text-max-width": "120px",
            width: 26,
            height: 26,
            "border-width": 2,
            "border-color": "#00000022",
          },
        },
        {
          selector: "node:selected",
          style: { "border-width": 4, "border-color": "#1f5fa9" },
        },
        {
          selector: "edge",
          style: {
            // Width encodes confidence, so strength survives a screenshot.
            width: (element: EdgeSingular) => 1 + (element.data("confidence") as number) * 4,
            "line-color": (element: EdgeSingular) =>
              (element.data("confidence") as number) >= 0.7 ? "#94a3b8" : "#cbd5e155",
            "line-style": (element: EdgeSingular) =>
              (element.data("confidence") as number) < 0.5 ? "dashed" : "solid",
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#94a3b8",
            "arrow-scale": 0.8,
            label: "data(label)",
            "font-size": "8px",
            color: "#8a8f98",
            "text-rotation": "autorotate",
          },
        },
        { selector: "edge:selected", style: { "line-color": "#1f5fa9", width: 4 } },
      ],
      layout: { name: "cose", animate: false, nodeDimensionsIncludeLabels: true },
      wheelSensitivity: 0.2,
    });

    core.on("tap", "node", (event) => {
      setSelected(event.target.data("raw") as GraphNode);
      setSelectionKind("node");
    });
    core.on("tap", "edge", (event) => {
      setSelected(event.target.data("raw") as GraphEdge);
      setSelectionKind("edge");
    });
    core.on("tap", (event) => {
      if (event.target === core) {
        setSelected(null);
        setSelectionKind(null);
      }
    });

    instance.current = core;
    return () => {
      core.destroy();
      instance.current = null;
    };
  }, [elements]);

  function expandNeighbours() {
    const core = instance.current;
    if (!core || selectionKind !== "node" || !selected) return;
    const node = core.getElementById(selected.id);
    core.elements().unselect();
    node.closedNeighborhood().select();
    core.animate({ fit: { eles: node.closedNeighborhood(), padding: 60 }, duration: 250 });
  }

  if (graph.nodes.length === 0) {
    return (
      <Empty
        title="Nothing to draw yet"
        hint="Entities and relationships appear once collectors have run."
      />
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <Button onClick={() => instance.current?.fit(undefined, 50)}>Fit</Button>
          <Button onClick={() => instance.current?.zoom(instance.current.zoom() * 1.3)}>
            Zoom in
          </Button>
          <Button onClick={() => instance.current?.zoom(instance.current.zoom() / 1.3)}>
            Zoom out
          </Button>
          <Button onClick={expandNeighbours} disabled={selectionKind !== "node"}>
            Expand neighbours
          </Button>
          <span className="ml-auto text-xs text-muted">
            {graph.nodes.length} nodes · {graph.edges.length} edges
          </span>
        </div>
        <div ref={container} className="h-[32rem] w-full bg-bg" role="img" aria-label="Relationship graph" />
        <div className="flex flex-wrap gap-2 border-t border-line px-3 py-2 text-[11px] text-muted">
          {Object.entries(graph.summary.entity_type_counts).map(([type, count]) => (
            <span key={type} className="inline-flex items-center gap-1">
              <span
                aria-hidden="true"
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: COLOURS[type] ?? "#64748b" }}
              />
              {humanise(type)} ({count})
            </span>
          ))}
          <span className="ml-auto">Thicker, solid edges mean higher confidence.</span>
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="text-sm font-semibold">Selection</h3>
        {!selected ? (
          <p className="mt-2 text-xs text-muted">
            Click a node or an edge to see what it is and what supports it.
          </p>
        ) : selectionKind === "node" ? (
          <NodeDetail node={selected as GraphNode} />
        ) : (
          <EdgeDetail edge={selected as GraphEdge} />
        )}
      </Card>
    </div>
  );
}

function NodeDetail({ node }: { node: GraphNode }) {
  return (
    <div className="mt-2 space-y-2 text-sm">
      <p className="font-medium">{node.label}</p>
      <div className="flex flex-wrap gap-1.5">
        <Badge>{humanise(node.type)}</Badge>
        <Badge tone={confidenceBand(node.confidence)}>{formatConfidence(node.confidence)}</Badge>
      </div>
      <dl className="space-y-1 text-xs text-muted">
        {Object.entries(node.attributes)
          .filter(([, value]) => value !== null && value !== undefined && value !== "")
          .slice(0, 12)
          .map(([key, value]) => (
            <div key={key} className="flex gap-2">
              <dt className="shrink-0 font-medium">{humanise(key)}</dt>
              <dd className="min-w-0 break-all">{String(value)}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}

function EdgeDetail({ edge }: { edge: GraphEdge }) {
  return (
    <div className="mt-2 space-y-2 text-sm">
      <p>
        <Mono>{edge.type}</Mono>
      </p>
      <div className="flex flex-wrap gap-1.5">
        <Badge tone={edge.strength}>{formatConfidence(edge.confidence)}</Badge>
        <Badge>{humanise(edge.strength)}</Badge>
      </div>
      {edge.reasons.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
          {edge.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-muted">
        Collector <Mono>{edge.collector}</Mono>
        {edge.evidence_ids.length
          ? ` · ${edge.evidence_ids.length} supporting finding(s)`
          : " · no supporting finding recorded"}
      </p>
    </div>
  );
}
