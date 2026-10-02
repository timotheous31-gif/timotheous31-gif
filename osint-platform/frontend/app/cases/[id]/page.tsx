"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { CandidateCard } from "@/components/case/candidate-card";
import { DeleteCase } from "@/components/case/delete-case";
import { useCaseWorkspace } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import {
  candidatePosture,
  graphSnapshot,
  latestJob,
  recordedDecisions,
  reviewState,
  suppliedContext,
  targetTypeLabel,
  timelinePreview,
  primarySubject,
} from "@/lib/case-workspace";
import { partitionCandidates, rankCandidates, withPlacements } from "@/lib/candidates";
import { formatConfidence, formatDateTime, humanise } from "@/lib/format";
import { displayState, stateHint, stateLabel, stateTone } from "@/lib/jobs";

/**
 * The case brief.
 *
 * Reading order on the left answers who this is about, what was found, where it
 * came from and what it rests on; the right column is operational state and the
 * review queue. Every panel is fed by an endpoint that exists — there is no
 * risk score, no priority and no threat level, because the platform computes
 * none of those and a confident-looking number nobody computed is worse than
 * no number.
 */
export default function CaseOverviewPage() {
  const router = useRouter();
  const { caseId, detail, summary, targets, runs, jobs, groups, personas, loading } =
    useCaseWorkspace();

  const findings = useAsync(() => api.listFindings(caseId, { limit: 6 }), [caseId]);
  const evidence = useAsync(() => api.listEvidence(caseId, { limit: 4 }), [caseId]);
  const graph = useAsync(() => api.graph(caseId), [caseId]);
  const timeline = useAsync(() => api.timeline(caseId, { limit: 50 }), [caseId]);

  if (loading || !detail) return <Spinner label="Loading overview" />;

  const { target, others } = primarySubject(targets);
  const context = suppliedContext(target);
  const posture = candidatePosture(groups);
  const decisions = recordedDecisions(groups);
  const job = latestJob(jobs);
  const state = displayState(job);
  const snapshot = graphSnapshot(graph.data);
  const events = timelinePreview(timeline.data?.events ?? []);
  const candidates = withPlacements(rankCandidates(personas), groups);
  const { primary } = partitionCandidates(candidates);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="min-w-0 space-y-4 lg:col-span-3">
        <Card>
          <CardHeader
            title="Subject"
            description="What the investigator supplied, and what the case is scoped to"
            action={
              <Link
                href={`/cases/${caseId}/targets`}
                className="text-[13px] text-accent hover:underline"
              >
                All targets →
              </Link>
            }
          />
          {target ? (
            <div className="space-y-3 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{target.raw_input}</span>
                <Badge tone="NEUTRAL">{targetTypeLabel(target.type)}</Badge>
                <Badge tone={target.status}>{target.status}</Badge>
                {others > 0 ? (
                  <span className="text-xs text-muted">
                    and {others} other target{others === 1 ? "" : "s"}
                  </span>
                ) : null}
              </div>
              {target.normalized_value !== target.raw_input ? (
                <p className="text-xs text-muted">
                  Normalised to <Mono>{target.normalized_value}</Mono>
                </p>
              ) : null}
              {context.length > 0 ? (
                <dl className="grid gap-2 sm:grid-cols-2">
                  {context.map((entry) => (
                    <div key={entry.label}>
                      <dt className="text-[10px] font-semibold uppercase tracking-label text-faint">
                        {entry.label}
                      </dt>
                      <dd className="mt-0.5 text-xs text-fg">{entry.values.join(", ")}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-xs text-muted">
                  No supporting context was supplied. Context — a username, an organisation, a
                  school, a city — is what lets a same-name record be corroborated, or ruled out.
                </p>
              )}
              {detail.notes ? (
                <p className="rounded border border-line bg-surface px-3 py-2 text-xs text-muted">
                  <span className="font-medium text-fg">Case note:</span> {detail.notes}
                </p>
              ) : null}
            </div>
          ) : (
            <div className="p-4">
              <Empty
                title="No target yet"
                hint="Add a target before running the investigation."
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Key findings"
            description="The strongest-scoring findings in the case, as the API ranks them"
            action={
              <Link
                href={`/cases/${caseId}/findings`}
                className="text-[13px] text-accent hover:underline"
              >
                All {summary?.findings ?? 0} →
              </Link>
            }
          />
          {findings.error ? (
            <div className="p-4">
              <ErrorNotice error={findings.error} retry={findings.reload} />
            </div>
          ) : findings.loading ? (
            <Spinner />
          ) : findings.data && findings.data.items.length > 0 ? (
            <ul className="divide-y divide-line">
              {findings.data.items.map((item) => (
                <li key={item.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-start gap-2">
                    <span className="min-w-0 flex-1 text-sm font-medium">{item.title}</span>
                    <Badge tone={item.classification}>{item.classification}</Badge>
                    <span
                      className="shrink-0 font-mono text-[12.5px] tabular-nums text-muted"
                      title="Correlation score — named rules combined, not a probability."
                    >
                      {formatConfidence(item.confidence)}
                    </span>
                  </div>
                  {item.summary ? (
                    <p className="mt-1 text-xs text-muted">{item.summary}</p>
                  ) : null}
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 font-mono text-[11px] text-faint">
                    <span>{item.collector}</span>
                    <span>{humanise(item.kind)}</span>
                    {item.source_url ? (
                      <a
                        href={item.source_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="break-anywhere hover:text-accent hover:underline"
                      >
                        {item.source_url}
                      </a>
                    ) : null}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <div className="p-4">
              <Empty title="No findings yet" hint="Run the investigation to collect data." />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Source coverage"
            description="Every execution, including failures and skips — a report is only as complete as this list"
          />
          {runs.length > 0 ? (
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
                {runs.map((run) => (
                  <tr key={run.id}>
                    <Td>
                      <Mono>{run.collector}</Mono>
                      <span className="ml-1 font-mono text-[11px] text-faint">
                        {run.collector_version}
                      </span>
                    </Td>
                    <Td>
                      <Badge tone={run.status}>{run.status}</Badge>
                    </Td>
                    <Td className="font-mono text-[12px] tabular-nums text-muted">
                      {run.duration_ms !== null ? `${Math.round(run.duration_ms)} ms` : "—"}
                    </Td>
                    <Td className="max-w-md text-xs text-muted">{run.error_message ?? "—"}</Td>
                    <Td className="whitespace-nowrap font-mono text-[11px] text-faint">
                      {formatDateTime(run.finished_at)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <div className="p-4">
              <Empty
                title="No collectors have run"
                hint="Use “Run investigation” in the header above."
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Recent evidence"
            description="The newest stored artefacts, each hashed when it was read"
            action={
              <Link
                href={`/cases/${caseId}/evidence`}
                className="text-[13px] text-accent hover:underline"
              >
                All {summary?.evidence ?? 0} →
              </Link>
            }
          />
          {evidence.loading ? (
            <Spinner />
          ) : evidence.data && evidence.data.items.length > 0 ? (
            <ul className="divide-y divide-line">
              {evidence.data.items.map((item) => (
                <li key={item.id} className="px-4 py-2.5">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <Mono className="font-semibold">{item.collector}</Mono>
                    <span className="font-mono text-[11px] text-faint">
                      {formatDateTime(item.retrieved_at)}
                    </span>
                    <Mono className="break-anywhere text-[11px] text-faint" title={item.sha256}>
                      {item.sha256.slice(0, 16)}…
                    </Mono>
                    {item.redacted ? <Badge tone="SENSITIVE">redacted</Badge> : null}
                  </div>
                  {item.source_url ? (
                    <p className="mt-0.5 break-anywhere font-mono text-[11px] text-muted">
                      {item.source_url}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <div className="p-4">
              <Empty title="No evidence stored yet" />
            </div>
          )}
        </Card>
      </div>

      <div className="min-w-0 space-y-4 lg:col-span-2">
        <Card>
          <CardHeader title="Latest run" description="What the worker last reported" />
          <div className="space-y-2 p-4">
            <div className="flex items-center justify-between gap-2">
              <Badge tone={stateTone(state) ?? "NEUTRAL"}>{stateLabel(state)}</Badge>
              {job ? (
                <Mono className="text-faint">{Math.round(job.progress * 100)}%</Mono>
              ) : null}
            </div>
            <p className="text-xs text-muted">{stateHint(state)}</p>
            {job?.message ? <p className="text-xs text-fg">{job.message}</p> : null}
            {job?.error_message ? (
              <p className="rounded border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger">
                {job.error_message}
              </p>
            ) : null}
            {job ? (
              <p className="font-mono text-[11px] text-faint">
                Started {formatDateTime(job.started_at)}
              </p>
            ) : null}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Candidate posture"
            description="Where the same-name records stand"
            action={
              <Link
                href={`/cases/${caseId}/candidates`}
                className="text-[13px] text-accent hover:underline"
              >
                Review →
              </Link>
            }
          />
          {posture.total === 0 ? (
            <div className="p-4">
              <Empty
                title="No candidates yet"
                hint="They appear once a person source has run."
              />
            </div>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-px bg-line">
                {[
                  ["Shown", posture.primary, "Corroborated, above threshold, or confirmed."],
                  ["Set aside", posture.lowConfidence, "Name-only. Kept, counted and one click away."],
                  ["Confirmed", posture.confirmed, "An analyst said this is the subject."],
                  ["Unreviewed", posture.unresolved, "No analyst decision recorded."],
                ].map(([label, value, hint]) => (
                  <div key={String(label)} className="bg-panel px-4 py-2.5" title={String(hint)}>
                    <dt className="text-[10px] font-semibold uppercase tracking-label text-faint">
                      {label}
                    </dt>
                    <dd className="mt-0.5 font-mono text-lg tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
              {primary.length > 0 ? (
                <div className="border-t border-line">
                  <CandidateCard candidate={primary[0]!} compact />
                </div>
              ) : null}
            </>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Analyst decisions"
            description="Human judgements, recorded beside the automated score and never merged into it"
          />
          {decisions.length === 0 ? (
            <div className="p-4">
              <Empty
                title="Nothing decided yet"
                hint="Every candidate still stands on the correlation rules alone."
              />
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {decisions.map((entry) => {
                const review = reviewState(entry.decision);
                return (
                  <li key={entry.key} className="px-4 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={review.tone} glyph={review.glyph} title={review.meaning}>
                        {review.label}
                      </Badge>
                      <span className="min-w-0 truncate text-xs font-medium">{entry.subject}</span>
                    </div>
                    {entry.note ? (
                      <p className="mt-1 text-xs text-muted">“{entry.note}”</p>
                    ) : null}
                    <p className="mt-0.5 font-mono text-[11px] text-faint">
                      {entry.by ?? "unknown"} — {formatDateTime(entry.at)}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Relationships"
            description="How the resolved entities connect"
            action={
              <Link
                href={`/cases/${caseId}/graph`}
                className="text-[13px] text-accent hover:underline"
              >
                Open graph →
              </Link>
            }
          />
          {graph.loading ? (
            <Spinner />
          ) : snapshot ? (
            <div className="space-y-3 p-4">
              <p className="font-mono text-[12.5px] text-muted">
                <span className="text-fg">{snapshot.nodes}</span> entities ·{" "}
                <span className="text-fg">{snapshot.edges}</span> relationships ·{" "}
                <span className="text-fg">{snapshot.components}</span> group
                {snapshot.components === 1 ? "" : "s"}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {snapshot.types.map(([type, count]) => (
                  <Badge key={type} tone="NEUTRAL">
                    {humanise(type)} · {count}
                  </Badge>
                ))}
              </div>
              <ul className="space-y-1.5">
                {snapshot.mostConnected.map((node) => (
                  <li key={node.id} className="flex items-baseline gap-2 text-xs">
                    {/* The bar is the node's degree against the best-connected
                        node — a real ratio of two stored numbers, not a score. */}
                    <span
                      aria-hidden="true"
                      className="h-1 shrink-0 rounded bg-accent"
                      style={{
                        width: `${Math.max(
                          8,
                          (node.degree / (snapshot.mostConnected[0]?.degree || 1)) * 56,
                        )}px`,
                      }}
                    />
                    <span className="min-w-0 flex-1 truncate font-medium">{node.label}</span>
                    <span className="font-mono text-[11px] text-faint">
                      {humanise(node.type)} · {node.degree}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="p-4">
              <Empty
                title="No relationships yet"
                hint="Entities connect once collectors have run and been resolved."
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Timeline"
            description="The most recent dated events"
            action={
              <Link
                href={`/cases/${caseId}/timeline`}
                className="text-[13px] text-accent hover:underline"
              >
                Full timeline →
              </Link>
            }
          />
          {timeline.loading ? (
            <Spinner />
          ) : events.length > 0 ? (
            <ol className="divide-y divide-line">
              {events.map((event) => (
                <li key={event.id} className="px-4 py-2.5">
                  <p className="font-mono text-[11px] text-faint">
                    {formatDateTime(event.occurred_at)}
                  </p>
                  <p className="mt-0.5 text-xs font-medium">{event.title}</p>
                  <p className="font-mono text-[11px] text-faint">
                    {humanise(event.kind)} · {event.collector}
                  </p>
                </li>
              ))}
            </ol>
          ) : (
            <div className="p-4">
              <Empty
                title="No dated events"
                hint="Events appear when a source publishes a date the platform can read."
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Case administration" />
          <div className="p-4">
            <DeleteCase
              caseId={caseId}
              caseName={detail.name}
              variant="button"
              onDeleted={() => router.push("/cases")}
            />
          </div>
        </Card>
      </div>
    </div>
  );
}
