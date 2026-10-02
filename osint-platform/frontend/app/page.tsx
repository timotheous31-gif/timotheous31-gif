"use client";

import Link from "next/link";

import { useSession } from "@/components/session";
import { PageHeader, HeaderMeta } from "@/components/ui/page-header";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Spinner,
  Stat,
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
import { formatDateTime } from "@/lib/format";
import { displayState, stateLabel, stateTone } from "@/lib/jobs";
import { can, PERMISSION } from "@/lib/permissions";

/**
 * The operations overview.
 *
 * Every panel is fed by an endpoint that exists. The two panels an overview
 * like this would normally carry — a feed of recent findings, and an analyst
 * review queue — are absent on purpose: findings and candidates are only
 * addressable per case, so there is no honest way to ask the API "what came in
 * across the workspace today". A panel that answered that question from a
 * sample of cases would look authoritative and be wrong.
 */
export default function DashboardPage() {
  const { workspace } = useSession();
  const canReadAudit = can(workspace, PERMISSION.auditRead);
  const workspaceId = workspace?.workspace.id;

  const cases = useAsync(() => api.listCases({ limit: 100 }), []);
  const collectors = useAsync(() => api.collectors(), []);
  const jobs = useAsync(() => api.jobs({ limit: 50 }), []);
  const health = useAsync(() => api.health(), []);
  const audit = useAsync(
    () => api.readAudit(workspaceId as string, { limit: 8 }),
    [workspaceId],
    { enabled: Boolean(workspaceId) && canReadAudit },
  );
  const members = useAsync(
    () => api.listMembers(workspaceId as string),
    [workspaceId],
    { enabled: Boolean(workspaceId) && canReadAudit },
  );

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
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        eyebrow="Operations"
        title="Dashboard"
        subtitle="This platform collects only information that is already published by its owner or by a public registry. Every finding is classified by a privacy filter and linked to hash-verified evidence."
        meta={
          <>
            <HeaderMeta label="Workspace" value={workspace?.workspace.name ?? "—"} />
            <HeaderMeta label="Role" value={workspace?.role ?? "—"} />
            <HeaderMeta
              label="API"
              value={health.data ? `${health.data.status} · ${health.data.version}` : "…"}
            />
          </>
        }
      />

      {cases.error ? <ErrorNotice error={cases.error} retry={cases.reload} /> : null}
      {jobs.error ? <ErrorNotice error={jobs.error} retry={jobs.reload} /> : null}

      <section aria-label="Posture" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Cases"
          value={cases.data?.total ?? "—"}
          hint={`${counts.NEW} new · ${counts.COMPLETE} complete`}
        />
        <Stat
          label="Running now"
          value={loading ? "—" : running.length}
          tone={running.length > 0 ? "INFO" : undefined}
          hint={running.length === 0 ? "No collector is running." : "Live collector runs"}
        />
        <Stat
          label="Needs attention"
          value={loading ? "—" : attention.items.length}
          tone={alerts > 0 ? "ALERT" : attention.items.length > 0 ? "CAUTION" : "OK"}
          hint={
            attention.items.length === 0
              ? "Nothing is waiting on you."
              : `${alerts} to act on now`
          }
        />
        <Stat
          label="Sources available"
          value={collectors.data ? `${posture.available}/${posture.total}` : "—"}
          tone={posture.blocked.length > 0 ? "CAUTION" : "OK"}
          hint={
            collectors.data
              ? `${posture.keyless} need no credential`
              : undefined
          }
          href="/collectors"
        />
      </section>

      <div className="grid gap-5 lg:grid-cols-5">
        <div className="min-w-0 space-y-5 lg:col-span-3">
          <Card>
            <CardHeader
              title="Needs attention"
              description="Runs that stopped, queues nothing is serving, and sources that cannot run"
            />
            {loading ? (
              <Spinner />
            ) : attention.items.length === 0 ? (
              <div className="p-4">
                <Empty
                  title="Nothing is waiting on you"
                  hint="No failed run, no stalled queue, no unconfigured source."
                />
              </div>
            ) : (
              <>
                <ul className="divide-y divide-line">
                  {attention.items.map((item) => {
                    const row = (
                      <span className="flex min-w-0 items-start gap-3">
                        <Badge
                          tone={item.severity}
                          className="mt-0.5 shrink-0"
                        >
                          {item.severity === "ALERT" ? "Alert" : "Check"}
                        </Badge>
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{item.title}</span>
                          <span className="mt-0.5 block text-xs text-muted">{item.reason}</span>
                          {item.at ? (
                            <span className="mt-0.5 block font-mono text-[11px] text-faint">
                              {formatDateTime(item.at)}
                            </span>
                          ) : null}
                        </span>
                      </span>
                    );
                    return (
                      <li key={item.key}>
                        {item.href ? (
                          <Link
                            href={item.href}
                            className="block px-4 py-3 transition-colors hover:bg-raised"
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
                  <p className="border-t border-line px-4 py-2 text-[11px] text-faint">
                    {attention.unresolved} more {attention.unresolved === 1 ? "job" : "jobs"} needs
                    attention in a case outside the most recent 100.
                  </p>
                ) : null}
              </>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Recent cases"
              description="The most recently created cases"
              action={
                <Link href="/cases" className="text-[13px] text-accent hover:underline">
                  All cases →
                </Link>
              }
            />
            {cases.loading ? (
              <Spinner />
            ) : caseList.length > 0 ? (
              <ul className="divide-y divide-line">
                {recentCases(caseList).map((item) => (
                  <li key={item.id}>
                    <Link
                      href={`/cases/${item.id}`}
                      className="flex items-center justify-between gap-3 px-4 py-2.5 transition-colors hover:bg-raised"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium">{item.name}</span>
                        <span className="block font-mono text-[11px] text-faint">
                          {formatDateTime(item.created_at)}
                        </span>
                      </span>
                      <Badge tone={item.status}>{item.status}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="p-4">
                <Empty title="No cases yet" hint="Create a case to start an investigation." />
              </div>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-5 lg:col-span-2">
          <Card>
            <CardHeader title="Active runs" description="Queued and running collections" />
            {jobs.loading ? (
              <Spinner />
            ) : running.length === 0 ? (
              <div className="p-4">
                <Empty title="Nothing is running" hint="Start a run from a case." />
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {running.map((job) => {
                  const state = displayState(job);
                  return (
                    <li key={job.id} className="px-4 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <Badge tone={stateTone(state) ?? "NEUTRAL"}>{stateLabel(state)}</Badge>
                        <Mono className="text-faint">{Math.round(job.progress * 100)}%</Mono>
                      </div>
                      <div
                        className="mt-2 h-1 w-full overflow-hidden rounded bg-raised"
                        role="progressbar"
                        aria-valuenow={Math.round(job.progress * 100)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label="Run progress"
                      >
                        <div
                          className="h-full bg-accent"
                          style={{ width: `${Math.round(job.progress * 100)}%` }}
                        />
                      </div>
                      {job.message ? (
                        <p className="mt-2 text-xs text-muted">{job.message}</p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Sources"
              description="What can run right now, and what cannot"
              action={
                <Link href="/collectors" className="text-[13px] text-accent hover:underline">
                  Details →
                </Link>
              }
            />
            {collectors.loading ? (
              <Spinner />
            ) : (
              <>
                <p className="px-4 py-3 text-sm text-muted">
                  <span className="font-mono text-fg tabular-nums">{posture.available}</span> of{" "}
                  <span className="font-mono text-fg tabular-nums">{posture.total}</span> collectors
                  are configured and able to run.
                </p>
                {posture.blocked.length > 0 ? (
                  <ul className="divide-y divide-line border-t border-line">
                    {posture.blocked.map((collector) => (
                      <li key={collector.name} className="px-4 py-3">
                        <Mono className="text-caution">{collector.name}</Mono>
                        <p className="mt-1 text-xs text-muted">{collector.unavailable_reason}</p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Workspace activity"
              description="From the workspace's audit log"
            />
            {!canReadAudit ? (
              <p className="px-4 py-4 text-xs text-muted">
                Your role in this workspace ({workspace?.role ?? "—"}) cannot read the audit log.
                An owner or admin can change that.
              </p>
            ) : audit.loading ? (
              <Spinner />
            ) : activity.length === 0 ? (
              <div className="p-4">
                <Empty title="Nothing logged yet" />
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {activity.map((line) => (
                  <li key={line.key} className="px-4 py-2.5">
                    <p className="text-[13px]">
                      <span className="font-medium">{line.what}</span>{" "}
                      <span className="text-muted">· {line.object}</span>
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-faint">
                      {line.who} — {formatDateTime(line.when)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
