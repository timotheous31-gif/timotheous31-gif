"use client";

import clsx from "clsx";
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
  Eyebrow,
  LiveDot,
  Mono,
  Readout,
  Spinner,
  Surface,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import {
  candidatePosture,
  graphSnapshot,
  latestJob,
  primarySubject,
  recordedDecisions,
  reviewState,
  suppliedContext,
  targetTypeLabel,
  timelinePreview,
} from "@/lib/case-workspace";
import { partitionCandidates, rankCandidates, withPlacements } from "@/lib/candidates";
import { formatConfidence, formatDateTime, humanise, padCount } from "@/lib/format";
import { displayState, stateHint, stateLabel, stateTone } from "@/lib/jobs";

/**
 * The case brief.
 *
 * Materials carry the reading order. The editorial column is *sheet* — subject,
 * findings, evidence: things you read. The operational column is *instrument* —
 * the live run, the candidate posture, the relationship shape: things you
 * check. Execution history and dated events are *telemetry*: a log tape you
 * scan. Nothing here is wrapped in the same card as everything else, because
 * that is exactly what made the page read as a template.
 *
 * Every panel is fed by an endpoint that exists. No risk score, no priority,
 * no threat level — the platform computes none of those.
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
    <div className="grid gap-5 lg:grid-cols-[1.75fr_1fr]">
      {/* --- editorial column: things you read ------------------------- */}
      <div className="min-w-0 space-y-5">
        <Card material="sheet">
          <CardHeader
            index="I"
            title="Subject"
            description="What the investigator supplied, and what the case is scoped to"
            action={
              <Link
                href={`/cases/${caseId}/targets`}
                className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
              >
                All targets →
              </Link>
            }
          />
          {target ? (
            <div className="space-y-3.5 px-5 py-4">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="text-[15px] font-semibold tracking-tight text-fg">
                  {target.raw_input}
                </span>
                <Badge tone="NEUTRAL">{targetTypeLabel(target.type)}</Badge>
                <Badge tone={target.status}>{target.status}</Badge>
                {others > 0 ? (
                  <span className="text-meta text-muted">
                    and {others} other target{others === 1 ? "" : "s"}
                  </span>
                ) : null}
              </div>
              {target.normalized_value !== target.raw_input ? (
                <p className="text-meta text-muted">
                  Normalised to <Mono className="text-fg">{target.normalized_value}</Mono>
                </p>
              ) : null}
              {context.length > 0 ? (
                <dl className="grid gap-x-8 gap-y-3 border-t border-rule-quiet pt-3.5 sm:grid-cols-2">
                  {context.map((entry) => (
                    <div key={entry.label}>
                      <dt className="eyebrow">{entry.label}</dt>
                      <dd className="mt-1 text-[13px] text-fg">{entry.values.join(", ")}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="border-t border-rule-quiet pt-3.5 text-meta text-muted">
                  No supporting context was supplied. Context — a username, an organisation, a
                  school, a city — is what lets a same-name record be corroborated, or ruled out.
                </p>
              )}
              {detail.notes ? (
                <p className="border-l-2 border-l-brass/60 bg-telemetry px-3.5 py-2.5 text-meta text-muted">
                  <span className="eyebrow eyebrow-brass !inline">Case note</span>{" "}
                  {detail.notes}
                </p>
              ) : null}
            </div>
          ) : (
            <Empty title="No target set" hint="Add a target before running the investigation." />
          )}
        </Card>

        <Card material="sheet">
          <CardHeader
            index="II"
            title="Key findings"
            description="The strongest-scoring findings in the case, as the API ranks them"
            action={
              <Link
                href={`/cases/${caseId}/findings`}
                className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
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
            <ul className="divide-y divide-rule-quiet">
              {findings.data.items.map((item) => (
                <li key={item.id} className="px-5 py-3.5">
                  <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                    <span className="min-w-0 flex-1 text-[13.5px] font-medium leading-snug text-fg">
                      {item.title}
                    </span>
                    <Badge tone={item.classification}>{item.classification}</Badge>
                    <Mono
                      className="shrink-0 tabular-nums text-muted"
                      title="Correlation score — named rules combined, not a probability."
                    >
                      {formatConfidence(item.confidence)}
                    </Mono>
                  </div>
                  {item.summary ? (
                    <p className="mt-1.5 text-meta text-muted">{item.summary}</p>
                  ) : null}
                  <p className="mt-1.5 flex flex-wrap items-center gap-x-3 font-mono text-[10.5px] text-faint">
                    <span className="text-accent-dim">{item.collector}</span>
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
            <Empty title="No findings yet" hint="Run the investigation to collect data." />
          )}
        </Card>

        {/* Telemetry: execution history is a log, not a feature. */}
        <Surface material="telemetry" className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-rule-quiet px-5 py-3">
            <div>
              <Eyebrow>Collector runs</Eyebrow>
              <p className="mt-1 text-[11px] text-muted">
                Every execution, including failures and skips — a report is only as complete as
                this list
              </p>
            </div>
            <Mono className="shrink-0 text-[10px] text-faint">EXEC</Mono>
          </div>
          {runs.length > 0 ? (
            <ul className="divide-y divide-rule-quiet/70">
              {runs.map((run) => (
                <li
                  key={run.id}
                  className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-2"
                >
                  <Mono className="w-28 shrink-0 text-[11px] text-fg">{run.collector}</Mono>
                  <Badge tone={run.status} className="shrink-0">
                    {run.status}
                  </Badge>
                  <Mono className="w-16 shrink-0 text-right text-[11px] tabular-nums text-faint">
                    {run.duration_ms !== null ? `${Math.round(run.duration_ms)}ms` : "—"}
                  </Mono>
                  <Mono className="shrink-0 text-[11px] text-faint">
                    {formatDateTime(run.finished_at)}
                  </Mono>
                  {run.error_message ? (
                    <span className="min-w-0 flex-1 text-[11px] text-caution">
                      {run.error_message}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty
              title="No collection executed"
              hint="Start an investigation run to populate this channel."
            />
          )}
        </Surface>

        <Card material="sheet">
          <CardHeader
            index="III"
            title="Recent evidence"
            description="The newest stored artefacts, each hashed when it was read"
            action={
              <Link
                href={`/cases/${caseId}/evidence`}
                className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
              >
                All {summary?.evidence ?? 0} →
              </Link>
            }
          />
          {evidence.loading ? (
            <Spinner />
          ) : evidence.data && evidence.data.items.length > 0 ? (
            <ul className="divide-y divide-rule-quiet">
              {evidence.data.items.map((item) => (
                <li key={item.id} className="px-5 py-3">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <Eyebrow tone="accent" className="!inline">
                      {item.collector}
                    </Eyebrow>
                    <Mono className="text-[10.5px] text-faint">
                      {formatDateTime(item.retrieved_at)}
                    </Mono>
                    <Mono className="text-[10.5px] text-faint" title={item.sha256}>
                      {item.sha256.slice(0, 12)}…
                    </Mono>
                    {item.redacted ? <Badge tone="SENSITIVE">redacted</Badge> : null}
                  </div>
                  {item.excerpt ? (
                    <p className="mt-1 line-clamp-2 text-meta text-muted">{item.excerpt}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No artefacts stored" />
          )}
        </Card>
      </div>

      {/* --- operational column: things you check ---------------------- */}
      <div className="min-w-0 space-y-5">
        <Card
          material="instrument"
          className={state === "RUNNING" || state === "QUEUED" ? "rail-live" : undefined}
        >
          <CardHeader title="Latest run" description="What the worker last reported" />
          <div className="space-y-2.5 px-4 py-4">
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-2">
                {state === "RUNNING" ? <LiveDot /> : null}
                <Badge tone={stateTone(state) ?? "NEUTRAL"}>{stateLabel(state)}</Badge>
              </span>
              {job ? (
                <Mono className="tabular-nums text-muted">
                  {Math.round(job.progress * 100)}%
                </Mono>
              ) : null}
            </div>
            <p className="text-meta text-muted">{stateHint(state)}</p>
            {job?.message ? <p className="text-meta text-fg">{job.message}</p> : null}
            {job?.error_message ? (
              <p className="border-l-2 border-l-danger bg-danger/5 px-3 py-2 text-meta text-danger">
                {job.error_message}
              </p>
            ) : null}
            {job ? (
              <p className="font-mono text-[10.5px] text-faint">
                Started {formatDateTime(job.started_at)}
              </p>
            ) : null}
          </div>
        </Card>

        <Card material="instrument">
          <CardHeader
            title="Candidate posture"
            description="Where the same-name records stand"
            action={
              <Link
                href={`/cases/${caseId}/candidates`}
                className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
              >
                Review →
              </Link>
            }
          />
          {posture.total === 0 ? (
            <Empty title="No candidates yet" hint="They appear once a person source has run." />
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-px bg-rule-quiet">
                {(
                  [
                    ["Shown", posture.primary, "Corroborated, above threshold, or confirmed.", undefined],
                    ["Set aside", posture.lowConfidence, "Name-only. Kept, counted and one click away.", undefined],
                    ["Confirmed", posture.confirmed, "An analyst said this is the subject.", "OK"],
                    ["Unreviewed", posture.unresolved, "No analyst decision recorded.", posture.unresolved > 0 ? "CAUTION" : undefined],
                  ] as const
                ).map(([label, value, hint, tone]) => (
                  <div key={label} className="bg-instrument px-4 py-3" title={hint}>
                    <Readout
                      size="sm"
                      label={label}
                      value={padCount(value)}
                      tone={tone as "OK" | "CAUTION" | undefined}
                    />
                  </div>
                ))}
              </dl>
              {primary.length > 0 ? (
                <div className="border-t border-rule-quiet">
                  <CandidateCard candidate={primary[0]!} compact />
                </div>
              ) : null}
            </>
          )}
        </Card>

        <Card material="sheet">
          <CardHeader
            index="IV"
            title="Analyst decisions"
            description="Human judgements, recorded beside the automated score and never merged into it"
          />
          {decisions.length === 0 ? (
            <Empty
              title="Nothing decided yet"
              hint="Every candidate still stands on the correlation rules alone."
            />
          ) : (
            <ul className="divide-y divide-rule-quiet">
              {decisions.map((entry) => {
                const review = reviewState(entry.decision);
                return (
                  <li key={entry.key} className="px-5 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={review.tone} glyph={review.glyph} title={review.meaning}>
                        {review.label}
                      </Badge>
                      <span className="min-w-0 truncate text-meta font-medium text-fg">
                        {entry.subject}
                      </span>
                    </div>
                    {entry.note ? (
                      <p className="mt-1.5 text-meta text-muted">“{entry.note}”</p>
                    ) : null}
                    <p className="mt-1 font-mono text-[10.5px] text-faint">
                      {entry.by ?? "unknown"} · {formatDateTime(entry.at)}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card material="instrument">
          <CardHeader
            title="Relationships"
            description="How the resolved entities connect"
            action={
              <Link
                href={`/cases/${caseId}/graph`}
                className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
              >
                Open graph →
              </Link>
            }
          />
          {graph.loading ? (
            <Spinner />
          ) : snapshot ? (
            <div className="px-4 py-4">
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                <Readout size="sm" label="Entities" value={snapshot.nodes} />
                <Readout size="sm" label="Links" value={snapshot.edges} />
                <Readout size="sm" label="Groups" value={snapshot.components} />
              </div>
              <div className="mt-3.5 flex flex-wrap gap-1.5">
                {snapshot.types.map(([type, count]) => (
                  <Badge key={type} tone="NEUTRAL">
                    {humanise(type)} · {count}
                  </Badge>
                ))}
              </div>
              {/* A well, on grid. The focal node is the best-connected one and
                  is drawn as such; the rest recede. The bars are a ratio of two
                  stored degrees — no topology is invented, and nothing is
                  placed on a map. */}
              <ul
                className="mt-3.5 space-y-2 rounded-sm px-3 py-3 shadow-recessed"
                style={{
                  backgroundColor: "rgb(var(--telemetry))",
                  backgroundImage:
                    "linear-gradient(to right, rgb(var(--rule-strong) / 0.18) 1px, transparent 1px), linear-gradient(to bottom, rgb(var(--rule-strong) / 0.18) 1px, transparent 1px)",
                  backgroundSize: "18px 18px",
                }}
              >
                {snapshot.mostConnected.map((node, position) => {
                  const top = snapshot.mostConnected[0]?.degree || 1;
                  const share = Math.max(0.12, node.degree / top);
                  const focal = position === 0;
                  return (
                    <li key={node.id} className="flex items-center gap-2.5">
                      <span
                        aria-hidden="true"
                        className={clsx(
                          "h-1.5 w-1.5 shrink-0 rounded-full",
                          focal
                            ? "bg-accent shadow-[0_0_8px_0_rgb(var(--accent)/0.8)]"
                            : "bg-edge",
                        )}
                      />
                      <span
                        className={clsx(
                          "min-w-0 flex-1 truncate text-[12px]",
                          focal ? "font-semibold text-fg" : "text-muted",
                        )}
                      >
                        {node.label}
                      </span>
                      <span
                        aria-hidden="true"
                        className={clsx("h-px", focal ? "bg-accent" : "bg-edge")}
                        style={{ width: `${Math.round(share * 44)}px` }}
                      />
                      <Mono className="w-4 shrink-0 text-right text-[10.5px] tabular-nums text-faint">
                        {node.degree}
                      </Mono>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <Empty
              title="No relationships mapped"
              hint="Entities connect once collectors have run and been resolved."
            />
          )}
        </Card>

        <Surface material="telemetry" className="overflow-hidden">
          <div className="flex items-center justify-between gap-2 border-b border-rule-quiet px-4 py-2.5">
            <Eyebrow>Timeline</Eyebrow>
            <Link
              href={`/cases/${caseId}/timeline`}
              className="text-[10px] font-semibold uppercase tracking-wide text-accent hover:underline"
            >
              Full →
            </Link>
          </div>
          {timeline.loading ? (
            <Spinner />
          ) : events.length > 0 ? (
            <ol className="divide-y divide-rule-quiet/70">
              {events.map((event) => (
                <li key={event.id} className="px-4 py-2">
                  <Mono className="block text-[10.5px] text-faint">
                    {formatDateTime(event.occurred_at)}
                  </Mono>
                  <p className="mt-0.5 text-[12px] text-fg">{event.title}</p>
                  <Mono className="text-[10.5px] text-faint">
                    {humanise(event.kind)} · {event.collector}
                  </Mono>
                </li>
              ))}
            </ol>
          ) : (
            <Empty
              title="No dated events"
              hint="Events appear when a source publishes a date the platform can read."
            />
          )}
        </Surface>

        <div className="pt-1">
          <DeleteCase
            caseId={caseId}
            caseName={detail.name}
            variant="button"
            onDeleted={() => router.push("/cases")}
          />
        </div>
      </div>
    </div>
  );
}
