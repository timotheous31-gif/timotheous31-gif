"use client";

import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Select,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { confidenceBand, formatConfidence, humanise } from "@/lib/format";

export default function EntitiesPage() {
  const caseId = useCaseId();
  const [type, setType] = useState("");

  const entities = useAsync(
    () => api.listEntities(caseId, { type: type || undefined, limit: 1000 }),
    [caseId, type],
  );
  const relationships = useAsync(
    () => api.listRelationships(caseId, { limit: 1000 }),
    [caseId],
  );

  const types = Array.from(new Set(entities.data?.items.map((item) => item.type) ?? [])).sort();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Filter by entity type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All entity types</option>
          {types.map((item) => (
            <option key={item} value={item}>
              {humanise(item)}
            </option>
          ))}
        </Select>
        <span className="ml-auto text-xs text-muted">
          {entities.data ? `${entities.data.total} entity(ies)` : ""}
        </span>
      </div>

      {entities.error ? <ErrorNotice error={entities.error} retry={entities.reload} /> : null}

      <Card>
        <CardHeader title="Entities" description="Resolved things, with the findings that support them" />
        {entities.loading ? (
          <Spinner />
        ) : entities.data && entities.data.items.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>Type</Th>
                <Th>Name</Th>
                <Th>Canonical value</Th>
                <Th>Confidence</Th>
                <Th>Supported by</Th>
              </tr>
            </thead>
            <tbody>
              {entities.data.items.map((entity) => (
                <tr key={entity.id}>
                  <Td>
                    <Badge>{humanise(entity.type)}</Badge>
                  </Td>
                  <Td className="font-medium">{entity.display_name}</Td>
                  <Td>
                    <Mono>{entity.canonical_value}</Mono>
                  </Td>
                  <Td>
                    <Badge tone={confidenceBand(entity.confidence)}>
                      {formatConfidence(entity.confidence)}
                    </Badge>
                  </Td>
                  <Td className="text-xs text-muted">
                    {entity.source_finding_ids.length} finding(s)
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No entities yet" hint="Entities are derived once collectors have run." />
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Relationships"
          description="Every edge states why it exists; a shared username is never an identity claim"
        />
        {relationships.loading ? (
          <Spinner />
        ) : relationships.data && relationships.data.items.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>From</Th>
                <Th>Relationship</Th>
                <Th>To</Th>
                <Th>Confidence</Th>
                <Th>Why</Th>
              </tr>
            </thead>
            <tbody>
              {relationships.data.items.map((edge) => (
                <tr key={edge.id}>
                  <Td>{edge.source_label ?? "—"}</Td>
                  <Td>
                    <Mono>{edge.type}</Mono>
                  </Td>
                  <Td>{edge.target_label ?? "—"}</Td>
                  <Td>
                    <Badge tone={edge.strength} title={edge.strength}>
                      {formatConfidence(edge.confidence)}
                    </Badge>
                  </Td>
                  <Td className="max-w-md text-xs text-muted">
                    {edge.confidence_reasons.join("; ") || "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No relationships yet" />
          </div>
        )}
      </Card>
    </div>
  );
}
