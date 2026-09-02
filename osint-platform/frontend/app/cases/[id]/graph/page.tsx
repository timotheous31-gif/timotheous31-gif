"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import { Card, ErrorNotice, Input, Select, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { humanise } from "@/lib/format";

// Cytoscape touches the DOM directly, so it must not run during SSR.
const GraphView = dynamic(() => import("@/components/case/graph-view").then((m) => m.GraphView), {
  ssr: false,
  loading: () => <Spinner label="Loading graph" />,
});

export default function GraphPage() {
  const caseId = useCaseId();
  const [minConfidence, setMinConfidence] = useState(0);
  const [entityType, setEntityType] = useState("");
  const [hideWeak, setHideWeak] = useState(false);

  const graph = useAsync(
    () =>
      api.graph(caseId, {
        min_confidence: hideWeak ? Math.max(minConfidence, 0.5) : minConfidence || undefined,
        types: entityType ? [entityType] : undefined,
      }),
    [caseId, minConfidence, entityType, hideWeak],
  );

  const types = Object.keys(graph.data?.summary.entity_type_counts ?? {}).sort();

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center gap-3 p-3">
        <label className="flex items-center gap-2 text-xs text-muted">
          Minimum edge confidence
          <Input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minConfidence}
            onChange={(event) => setMinConfidence(Number(event.target.value))}
            className="w-40"
            aria-label="Minimum edge confidence"
          />
          <span className="tabular-nums">{minConfidence.toFixed(2)}</span>
        </label>

        <Select
          aria-label="Filter by entity type"
          value={entityType}
          onChange={(event) => setEntityType(event.target.value)}
        >
          <option value="">All entity types</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {humanise(type)}
            </option>
          ))}
        </Select>

        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={hideWeak}
            onChange={(event) => setHideWeak(event.target.checked)}
          />
          Hide weak associations (&lt; 0.50)
        </label>
      </Card>

      {graph.error ? <ErrorNotice error={graph.error} retry={graph.reload} /> : null}
      {graph.loading ? <Spinner label="Loading graph" /> : null}
      {graph.data ? <GraphView graph={graph.data} /> : null}
    </div>
  );
}
