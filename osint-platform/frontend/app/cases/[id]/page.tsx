"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { DeleteCase } from "@/components/case/delete-case";
import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Spinner,
  Stat,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime, humanise } from "@/lib/format";

export default function CaseOverviewPage() {
  const router = useRouter();
  const caseId = useCaseId();
  const summary = useAsync(() => api.caseSummary(caseId), [caseId]);
  const runs = useAsync(() => api.listRuns(caseId), [caseId]);
  const graph = useAsync(() => api.graph(caseId), [caseId]);

  if (summary.error) return <ErrorNotice error={summary.error} retry={summary.reload} />;
  if (summary.loading || !summary.data) return <Spinner label="Loading overview" />;

  const data = summary.data;
  const distribution = data.confidence_distribution;
  const totalFindings = distribution.high + distribution.medium + distribution.low;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{data.case.name}</h2>
          <p className="mt-0.5 text-xs text-muted">
            Created {formatDateTime(data.case.created_at)}
          </p>
        </div>
        {/* Destructive actions live apart from everything else on the page. */}
        <DeleteCase
          caseId={caseId}
          caseName={data.case.name}
          variant="button"
          onDeleted={() => router.push("/cases")}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Targets" value={data.targets} />
        <Stat label="Findings" value={data.findings} />
        <Stat label="Entities" value={data.entities} />
        <Stat label="Relationships" value={data.relationships} />
        <Stat label="Evidence" value={data.evidence} />
        <Stat label="Timeline" value={data.timeline_events} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Confidence distribution"
            description="How findings are spread across confidence bands"
          />
          <div className="space-y-3 p-4">
            {totalFindings === 0 ? (
              <Empty title="No findings yet" hint="Run the investigation to collect data." />
            ) : (
              (
                [
                  ["high", "≥ 0.80", "bg-likely", distribution.high],
                  ["medium", "0.50 – 0.79", "bg-probable", distribution.medium],
                  ["low", "< 0.50", "bg-weak", distribution.low],
                ] as const
              ).map(([key, range, colour, count]) => (
                <div key={key}>
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">
                      {humanise(key)} <span className="text-muted">({range})</span>
                    </span>
                    <span className="tabular-nums text-muted">{count}</span>
                  </div>
                  <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-line">
                    <div
                      className={`h-full ${colour}`}
                      style={{ width: `${totalFindings ? (count / totalFindings) * 100 : 0}%` }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Graph preview"
            description="Entity types and the best-connected nodes"
            action={
              <Link href={`/cases/${caseId}/graph`} className="text-sm text-accent hover:underline">
                Open graph →
              </Link>
            }
          />
          <div className="p-4">
            {graph.loading ? (
              <Spinner />
            ) : graph.data && graph.data.nodes.length > 0 ? (
              <div className="space-y-3 text-sm">
                <p className="text-muted">
                  {graph.data.summary.node_count} entities, {graph.data.summary.edge_count}{" "}
                  relationships, {graph.data.summary.component_count} connected group(s).
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(graph.data.summary.entity_type_counts).map(([type, count]) => (
                    <Badge key={type}>
                      {humanise(type)} · {count}
                    </Badge>
                  ))}
                </div>
                {graph.data.summary.most_connected.length > 0 ? (
                  <ul className="space-y-1 text-xs text-muted">
                    {graph.data.summary.most_connected.map((node) => (
                      <li key={node.id}>
                        <span className="font-medium text-fg">{node.label}</span> ·{" "}
                        {humanise(node.type)} · {node.degree} connection(s)
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : (
              <Empty title="No graph yet" hint="Entities appear once collectors have run." />
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Collector runs"
          description="Every execution, including failures and skips — a report is only as complete as this list"
        />
        {runs.loading ? (
          <Spinner />
        ) : runs.data && runs.data.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>Collector</Th>
                <Th>Status</Th>
                <Th>Duration</Th>
                <Th>Detail</Th>
                <Th>Finished</Th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((run) => (
                <tr key={run.id}>
                  <Td className="font-mono text-[13px]">
                    {run.collector}
                    <span className="ml-1 text-muted">{run.collector_version}</span>
                  </Td>
                  <Td>
                    <Badge tone={run.status}>{run.status}</Badge>
                  </Td>
                  <Td className="tabular-nums text-muted">
                    {run.duration_ms !== null ? `${Math.round(run.duration_ms)} ms` : "—"}
                  </Td>
                  <Td className="max-w-md text-xs text-muted">
                    {run.error_message ?? "—"}
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDateTime(run.finished_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No collectors have run" hint="Use “Run investigation” above." />
          </div>
        )}
      </Card>
    </div>
  );
}
