/**
 * What the overview is allowed to say.
 *
 * Every function here reads rows the platform already stores and returns a
 * arrangement of them. Nothing is scored, weighted, extrapolated or estimated:
 * a dashboard that invents a number is worse than a dashboard with a gap in
 * it, because the gap is honest. Where the API cannot answer a question — a
 * workspace-wide feed of findings, say — the question is simply not asked.
 *
 * It lives in `lib/` rather than in the page for one practical reason: the
 * repository's test runner has no DOM, so logic that is testable has to be
 * logic that is not a component.
 */

import { displayState, isActive } from "@/lib/jobs";
import { humanise } from "@/lib/format";
import type {
  AuditEntry,
  Case,
  CaseStatus,
  CollectorInfo,
  EffectiveJobState,
  Job,
  Membership,
} from "@/types/api";

/** Case statuses in the order an operator reads them. */
export const CASE_STATUS_ORDER: CaseStatus[] = [
  "RUNNING",
  "NEW",
  "PAUSED",
  "COMPLETE",
  "ARCHIVED",
];

export type Severity = "ALERT" | "CAUTION";

export interface AttentionItem {
  /** Stable across reloads, so React keys do not shuffle. */
  key: string;
  title: string;
  /** What is wrong, in the platform's own words. */
  reason: string;
  severity: Severity;
  href?: string;
  /** When the underlying row last changed, for ordering. */
  at: string | null;
}

export interface Attention {
  items: AttentionItem[];
  /**
   * Jobs whose case is not in the loaded page of cases. Counted rather than
   * guessed at: naming a case we did not fetch would mean printing a raw id.
   */
  unresolved: number;
}

export function countByStatus(cases: Case[]): Record<CaseStatus, number> {
  const counts = {
    NEW: 0,
    RUNNING: 0,
    PAUSED: 0,
    COMPLETE: 0,
    ARCHIVED: 0,
  } as Record<CaseStatus, number>;
  for (const item of cases) {
    if (item.status in counts) counts[item.status] += 1;
  }
  return counts;
}

/**
 * Jobs something is actually acting on, newest first.
 *
 * A job whose queue has no worker is deliberately *not* here. Its stored state
 * is still QUEUED — the row is a standing instruction a returning worker must
 * be able to run — but nothing is serving it, and listing it as active would
 * tell an operator that work is under way when none is. It surfaces under
 * `needsAttention` instead, where it can be acted on.
 */
export function activeJobs(jobs: Job[]): Job[] {
  return jobs
    .filter((job) => isActive(displayState(job)))
    .sort((left, right) => right.created_at.localeCompare(left.created_at));
}

/** The most recently created cases, newest first. */
export function recentCases(cases: Case[], limit = 6): Case[] {
  return [...cases]
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .slice(0, limit);
}

export interface CollectorPosture {
  total: number;
  available: number;
  blocked: CollectorInfo[];
  /** Available without any credential at all — the platform's free floor. */
  keyless: number;
}

export function collectorPosture(collectors: CollectorInfo[]): CollectorPosture {
  const blocked = collectors.filter((item) => !item.available);
  return {
    total: collectors.length,
    available: collectors.length - blocked.length,
    blocked,
    keyless: collectors.filter((item) => item.available && !item.requires_api_key).length,
  };
}

const JOB_ATTENTION: Partial<Record<EffectiveJobState, { severity: Severity; reason: string }>> = {
  FAILED: { severity: "ALERT", reason: "The last run stopped on an error." },
  PROCESSING_UNAVAILABLE: {
    severity: "ALERT",
    reason:
      "Queued, but no worker is consuming the queue — the run cannot start until one comes back.",
  },
};

/**
 * Investigations and platform conditions that want a person.
 *
 * Derived entirely from stored state: a job's recorded state and error, a
 * case's status, a collector's own report of why it cannot run. There is no
 * threshold, no staleness heuristic and no scoring — each item is something
 * the platform already knows to be true.
 */
export function needsAttention({
  cases,
  jobs,
  collectors,
}: {
  cases: Case[];
  jobs: Job[];
  collectors: CollectorInfo[];
}): Attention {
  const byId = new Map(cases.map((item) => [item.id, item]));
  const items: AttentionItem[] = [];
  let unresolved = 0;

  for (const job of jobs) {
    const rule = JOB_ATTENTION[displayState(job)];
    if (!rule) continue;
    const owner = byId.get(job.case_id);
    if (!owner) {
      unresolved += 1;
      continue;
    }
    items.push({
      key: `job:${job.id}`,
      title: owner.name,
      reason: job.error_message ? `${rule.reason} ${job.error_message}` : rule.reason,
      severity: rule.severity,
      href: `/cases/${owner.id}`,
      at: job.finished_at ?? job.started_at ?? job.created_at,
    });
  }

  const flagged = new Set(items.map((item) => item.title));
  for (const item of cases) {
    if (item.status !== "PAUSED" || flagged.has(item.name)) continue;
    items.push({
      key: `case:${item.id}`,
      title: item.name,
      reason: "Paused. Nothing will collect for it until it is resumed.",
      severity: "CAUTION",
      href: `/cases/${item.id}`,
      at: item.updated_at,
    });
  }

  for (const collector of collectors.filter((entry) => !entry.available)) {
    items.push({
      key: `collector:${collector.name}`,
      title: `Source unavailable: ${collector.name}`,
      reason: collector.unavailable_reason,
      severity: "CAUTION",
      href: "/collectors",
      at: null,
    });
  }

  items.sort((left, right) => {
    if (left.severity !== right.severity) return left.severity === "ALERT" ? -1 : 1;
    return (right.at ?? "").localeCompare(left.at ?? "");
  });

  return { items, unresolved };
}

export interface ActivityLine {
  key: string;
  /** The event, in words. */
  what: string;
  /** Who did it, or "—" when the log records no actor. */
  who: string;
  when: string;
  object: string;
}

/**
 * The workspace's security log, rendered as sentences.
 *
 * `actor_user_id` is a raw uuid in the API, so the caller supplies the
 * workspace's members and the id is resolved against them. An actor who is no
 * longer a member resolves to nothing, and the line says so rather than
 * printing the uuid at a reader.
 */
export function recentActivity(
  entries: AuditEntry[],
  members: Membership[],
  limit = 8,
): ActivityLine[] {
  const names = new Map(
    members.map((member) => [member.user.id, member.user.display_name || member.user.email]),
  );
  return entries.slice(0, limit).map((entry) => ({
    key: entry.id,
    what: humanise(entry.event_type),
    who: entry.actor_user_id ? (names.get(entry.actor_user_id) ?? "A former member") : "—",
    when: entry.occurred_at,
    object: humanise(entry.object_type),
  }));
}
