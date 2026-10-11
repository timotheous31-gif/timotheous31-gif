"use client";

import Link from "next/link";

import { useSession } from "@/components/session";
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
  activeJobs,
  collectorPosture,
  countByStatus,
  needsAttention,
  recentActivity,
  recentCases,
} from "@/lib/dashboard";
import { formatDateTime, padCount } from "@/lib/format";
import { displayState, stateLabel, stateTone } from "@/lib/jobs";
import { can, PERMISSION } from "@/lib/permissions";

/**
 * The operations overview.
 *
 * One command surface establishes where you are and what the workspace holds;
 * everything under it is deliberately asymmetric — a wide operational column
 * and a narrow instrument column — because four equal cards is the shape of an
 * admin template, not of a console.
 *
 * Every panel is fed by an endpoint that exists. The two an overview like this
 * would normally carry — a feed of recent findings, and an analyst review
 * queue — are absent on purpose: findings and candidates are addressable per
 * case only, so there is no honest way to ask the API what came in across the
 * workspace today. A panel answering that from a sample of cases would look
 * authoritative and be wrong.
 */
export default function DashboardPage() {
  const { workspace } = useSession();
  const canReadAudit = can(workspace, PERMISSION.auditRead);
  const workspaceId = workspace?.workspace.id;

  const cases = useAsync(() => api.listCases({ limit: 100 }), []);
  const collectors = useAsync(() => api.collectors(), []);
  const jobs = useAsync(() => api.jobs({ limit: 50 }), []);
  const health = useAsync(() => api.health(), []);
  const audit = useAsync(() => api.readAudit(workspaceId as string, { limit: 8 }), [workspaceId], {
    enabled: Boolean(workspaceId) && canReadAudit,
  });
  const members = useAsync(() => api.listMembers(workspaceId as string), [workspaceId], {
    enabled: Boolean(workspaceId) && canReadAudit,
  });

  const caseList = cases.data?.items ?? [];
  const counts = countByStatus(caseList);
  const posture = collectorPosture(collectors.data ?? []);
  const running = activeJobs(jobs.data ?? []);
  const attention = needsAttention({
    cases: caseList,
    jobs: jobs.data ?? [],
    collectors: collectors.data ?? [],
  });
  const alerts = attention.items.filter((item) => item.severity === "ALERT").length;
  const activity = recentActivity(audit.data ?? [], members.data ?? []);
  const loading = cases.loading || collectors.loading || jobs.loading;

  return (
    <div className="mx-auto max-w-[1180px] space-y-5">
      {/* --- command ---------------------------------------------------- */}
      <Surface material="command" notch className="overflow-hidden">
        <div className="px-6 pb-5 pt-6 sm:px-8 sm:pt-7">
          <Eyebrow tone="accent">Operations · Intelligence control</Eyebrow>
          <h1 className="mt-3 text-display text-fg">{workspace?.workspace.name ?? "Workspace"}</h1>
          <p className="mt-1.5 text-lede text-muted">Public-source intelligence workspace</p>
        </div>

        <div className="relative grid grid-cols-2 sm:grid-cols-4 before:absolute before:inset-x-6 before:top-0 before:h-px before:bg-gradient-to-r before:from-rule before:via-rule before:to-transparent after:absolute after:inset-x-6 after:bottom-0 after:h-px after:bg-gradient-to-r after:from-rule after:to-transparent sm:before:inset-x-8 sm:after:inset-x-8">
          {[
            {
              label: "Investigations",
              value: padCount(cases.data?.total ?? "—"),
              hint: `${counts.NEW} new · ${counts.COMPLETE} complete`,
              href: "/cases",
            },
            {
              label: "Active",
              value: loading ? "—" : padCount(running.length),
              tone: running.length > 0 ? ("INFO" as const) : undefined,
              hint: running.length === 0 ? "No collector running" : "Live collector runs",
            },
            {
              label: "Attention",
              value: loading ? "—" : padCount(attention.items.length),
              tone: alerts > 0 ? ("ALERT" as const) : attention.items.length > 0 ? ("CAUTION" as const) : undefined,
              hint: attention.items.length === 0 ? "Nothing waiting on you" : `${alerts} to act on now`,
            },
            {
              label: "Sources",
              value: collectors.data ? `${posture.available}/${posture.total}` : "—",
              tone: posture.blocked.length > 0 ? ("CAUTION" as const) : undefined,
              hint: collectors.data ? `${posture.keyless} need no credential` : undefined,
              href: "/collectors",
            },
          ].map((cell) => (
            <div
              key={cell.label}
              className="relative px-6 py-5 sm:px-8 [&:not(:first-child)]:before:absolute [&:not(:first-child)]:before:inset-y-5 [&:not(:first-child)]:before:left-0 [&:not(:first-child)]:before:w-px [&:not(:first-child)]:before:bg-gradient-to-b [&:not(:first-child)]:before:from-transparent [&:not(:first-child)]:before:via-rule [&:not(:first-child)]:before:to-transparent"
            >
              <Readout
                size="lg"
                label={cell.label}
                value={cell.value}
                hint={cell.hint}
                tone={cell.tone}
                href={cell.href}
              />
            </div>
          ))}
        </div>

        {/* The system line: the quietest row on the loudest surface. */}
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-rule-quiet bg-telemetry/50 px-6 py-2.5 sm:px-8">
          <span className="inline-flex items-center gap-2">
            <LiveDot live={health.data?.status === "ok"} />
            <Eyebrow>{health.data ? "System online" : "Contacting system"}</Eyebrow>
          </span>
          <span className="inline-flex items-center gap-5">
            <Eyebrow tone="brass">{workspace?.role ?? "—"}</Eyebrow>
            <Mono className="text-faint">v{health.data?.version ?? "—"}</Mono>
          </span>
        </div>
      </Surface>

      {cases.error ? <ErrorNotice error={cases.error} retry={cases.reload} /> : null}
      {jobs.error ? <ErrorNotice error={jobs.error} retry={jobs.reload} /> : null}

      {/* --- asymmetric body: 65 / 35 ------------------------------------ */}
      <div className="grid gap-5 lg:grid-cols-[1.9fr_1fr]">
        <div className="min-w-0 space-y-5">
          <Card material="instrument">
            <CardHeader
              index="01"
              title="Needs attention"
              description="Runs that stopped, queues nothing is serving, and sources that cannot run"
            />
            {loading ? (
              <Spinner />
            ) : attention.items.length === 0 ? (
              <Empty
                title="All channels nominal"
                hint="No failed run, no stalled queue, no unconfigured source."
              />
            ) : (
              <>
                <ul className="divide-y divide-rule-quiet">
                  {attention.items.map((item) => {
                    const row = (
                      <span className="flex min-w-0 items-start gap-3">
                        <Badge tone={item.severity} className="mt-0.5 shrink-0">
                          {item.severity === "ALERT" ? "Alert" : "Check"}
                        </Badge>
                        <span className="min-w-0">
                          <span className="block truncate text-[13.5px] font-medium text-fg">
                            {item.title}
                          </span>
                          <span className="mt-1 block text-meta text-muted">{item.reason}</span>
                          {item.at ? (
                            <span className="mt-1 block font-mono text-[11px] text-faint">
                              {formatDateTime(item.at)}
                            </span>
                          ) : null}
                        </span>
                      </span>
                    );
                    return (
                      <li
                        key={item.key}
                        className={
                          item.severity === "ALERT"
                            ? "border-l-2 border-l-danger"
                            : "border-l-2 border-l-caution"
                        }
                      >
                        {item.href ? (
                          <Link
                            href={item.href}
                            className="block px-4 py-3 transition-colors hover:bg-raised/60"
                          >
                            {row}
                          </Link>
                        ) : (
                          <div className="px-4 py-3">{row}</div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {attention.unresolved > 0 ? (
                  <p className="border-t border-rule-quiet px-4 py-2 text-[11px] text-faint">
                    {attention.unresolved} more {attention.unresolved === 1 ? "job" : "jobs"} needs
                    attention in a case outside the most recent 100.
                  </p>
                ) : null}
              </>
            )}
          </Card>

          <Card material="sheet">
            <CardHeader
              index="02"
              title="Investigations"
              description="The most recently opened cases"
              action={
                <Link
                  href="/cases"
                  className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
                >
                  All →
                </Link>
              }
            />
            {cases.loading ? (
              <Spinner />
            ) : caseList.length > 0 ? (
              <ul className="divide-y divide-rule-quiet">
                {recentCases(caseList).map((item) => (
                  <li key={item.id}>
                    <Link
                      href={`/cases/${item.id}`}
                      className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-raised/40"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13.5px] font-medium text-fg">
                          {item.name}
                        </span>
                        <span className="mt-0.5 block font-mono text-[11px] text-faint">
                          {formatDateTime(item.created_at)}
                        </span>
                      </span>
                      <Badge tone={item.status}>{item.status}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No investigations open" hint="Create a case to begin." />
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-5">
          <Card material="instrument" className={running.length > 0 ? "rail-live" : undefined}>
            <CardHeader title="Active runs" description="Queued and running collections" />
            {jobs.loading ? (
              <Spinner />
            ) : running.length === 0 ? (
              <Empty
                title="No active collection"
                hint="Start an investigation run to populate this channel."
              />
            ) : (
              <ul className="divide-y divide-rule-quiet">
                {running.map((job) => {
                  const state = displayState(job);
                  const percent = Math.round(job.progress * 100);
                  return (
                    <li key={job.id} className="px-4 py-3.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="inline-flex items-center gap-2">
                          <LiveDot live={state === "RUNNING"} />
                          <Badge tone={stateTone(state) ?? "NEUTRAL"}>{stateLabel(state)}</Badge>
                        </span>
                        <Mono className="tabular-nums text-muted">{percent}%</Mono>
                      </div>
                      <div
                        className="mt-2.5 h-[3px] w-full overflow-hidden rounded-sm bg-telemetry shadow-recessed"
                        role="progressbar"
                        aria-valuenow={percent}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label="Run progress"
                      >
                        <div
                          className="h-full bg-accent shadow-[0_0_10px_0_rgb(var(--accent)/0.7)]"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                      {job.message ? (
                        <p className="mt-2 text-meta text-muted">{job.message}</p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card material="instrument">
            <CardHeader
              title="Source state"
              description="What can run now, and what cannot"
              action={
                <Link
                  href="/collectors"
                  className="text-[11px] font-semibold uppercase tracking-wide text-accent hover:underline"
                >
                  Detail →
                </Link>
              }
            />
            {collectors.loading ? (
              <Spinner />
            ) : (
              <>
                <div className="flex items-baseline gap-3 px-4 py-3.5">
                  <Readout
                    size="sm"
                    label="Configured"
                    value={`${posture.available}/${posture.total}`}
                    tone={posture.blocked.length > 0 ? "CAUTION" : undefined}
                  />
                </div>
                {posture.blocked.length > 0 ? (
                  <ul className="divide-y divide-rule-quiet border-t border-rule-quiet">
                    {posture.blocked.map((collector) => (
                      <li key={collector.name} className="border-l-2 border-l-caution px-4 py-3">
                        <Mono className="text-caution">{collector.name}</Mono>
                        <p className="mt-1 text-meta text-muted">
                          {collector.unavailable_reason}
                        </p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
          </Card>

          {/* Telemetry: a log tape, not a card. */}
          <Surface material="telemetry" className="overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-4 pb-2 pt-3.5">
              <Eyebrow>Workspace activity</Eyebrow>
              <Mono className="text-[10px] text-faint">AUDIT</Mono>
            </div>
            {!canReadAudit ? (
              <p className="px-4 pb-4 text-meta text-muted">
                Your role in this workspace ({workspace?.role ?? "—"}) cannot read the audit log.
                An owner or admin can change that.
              </p>
            ) : audit.loading ? (
              <Spinner />
            ) : activity.length === 0 ? (
              <Empty title="No activity recorded" />
            ) : (
              <ul className="divide-y divide-rule-quiet/60 pb-1">
                {activity.map((line) => (
                  <li key={line.key} className="px-4 py-2">
                    <p className="font-mono text-[11px] leading-relaxed text-muted">
                      <span className="text-faint">{formatDateTime(line.when).slice(11)}</span>{" "}
                      <span className="text-fg">{line.what}</span>{" "}
                      <span className="text-faint">· {line.who}</span>
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Surface>
        </div>
      </div>
    </div>
  );
}
