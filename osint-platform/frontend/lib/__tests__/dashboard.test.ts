import { describe, expect, it } from "vitest";

import {
  activeJobs,
  collectorPosture,
  countByStatus,
  needsAttention,
  recentActivity,
  recentCases,
} from "@/lib/dashboard";
import type { AuditEntry, Case, CollectorInfo, Job, Membership } from "@/types/api";

function makeCase(overrides: Partial<Case> & Pick<Case, "id" | "name" | "status">): Case {
  return {
    description: null,
    notes: null,
    tags: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as Case;
}

function makeJob(overrides: Partial<Job> & Pick<Job, "id" | "case_id" | "state">): Job {
  return {
    celery_id: null,
    progress: 0,
    message: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    params: {},
    result: {},
    cancel_requested: false,
    created_at: "2026-01-01T00:00:00Z",
    effective_state: "",
    processing_available: true,
    ...overrides,
  } as Job;
}

function makeCollector(overrides: Partial<CollectorInfo> & Pick<CollectorInfo, "name">): CollectorInfo {
  return {
    version: "1.0.0",
    description: "",
    supported_targets: [],
    requires_api_key: false,
    rate_limit: "",
    timeout: 10,
    run_timeout: null,
    source_attribution: "",
    network: true,
    available: true,
    unavailable_reason: "",
    configuration: {} as CollectorInfo["configuration"],
    ...overrides,
  } as CollectorInfo;
}

describe("countByStatus", () => {
  it("counts every status, including the ones with no cases", () => {
    const counts = countByStatus([
      makeCase({ id: "1", name: "a", status: "RUNNING" }),
      makeCase({ id: "2", name: "b", status: "RUNNING" }),
      makeCase({ id: "3", name: "c", status: "ARCHIVED" }),
    ]);
    expect(counts.RUNNING).toBe(2);
    expect(counts.ARCHIVED).toBe(1);
    expect(counts.NEW).toBe(0);
    expect(counts.PAUSED).toBe(0);
    expect(counts.COMPLETE).toBe(0);
  });
});

describe("activeJobs", () => {
  it("keeps queued and running work, newest first", () => {
    const rows = activeJobs([
      makeJob({ id: "done", case_id: "c", state: "COMPLETE" }),
      makeJob({ id: "old", case_id: "c", state: "RUNNING", created_at: "2026-01-01T00:00:00Z" }),
      makeJob({ id: "new", case_id: "c", state: "QUEUED", created_at: "2026-02-01T00:00:00Z" }),
      makeJob({ id: "cancelled", case_id: "c", state: "CANCELLED" }),
    ]);
    expect(rows.map((job) => job.id)).toEqual(["new", "old"]);
  });

  it("leaves a queue nothing is serving out of the active list", () => {
    // The stored state stays QUEUED on purpose — the row is a standing
    // instruction a returning worker must still be able to run — but nothing
    // is acting on it, so calling it "active" would be a claim the platform
    // cannot support. It surfaces under `needsAttention` instead, which is
    // where an operator can do something about it.
    const rows = activeJobs([
      makeJob({
        id: "stalled",
        case_id: "c",
        state: "QUEUED",
        effective_state: "PROCESSING_UNAVAILABLE",
        processing_available: false,
      }),
    ]);
    expect(rows).toEqual([]);
  });
});

describe("collectorPosture", () => {
  it("separates what can run from what cannot, and counts the free floor", () => {
    const posture = collectorPosture([
      makeCollector({ name: "dns_basic" }),
      makeCollector({ name: "github", requires_api_key: true }),
      makeCollector({
        name: "search",
        available: false,
        unavailable_reason: "No search provider is configured.",
      }),
    ]);
    expect(posture.total).toBe(3);
    expect(posture.available).toBe(2);
    expect(posture.keyless).toBe(1);
    expect(posture.blocked.map((item) => item.name)).toEqual(["search"]);
  });
});

describe("needsAttention", () => {
  const paused = makeCase({
    id: "p",
    name: "Paused matter",
    status: "PAUSED",
    updated_at: "2026-03-01T00:00:00Z",
  });
  const live = makeCase({ id: "l", name: "Live matter", status: "RUNNING" });

  it("raises a failed run as an alert and quotes the stored error", () => {
    const { items } = needsAttention({
      cases: [live],
      jobs: [
        makeJob({
          id: "j",
          case_id: "l",
          state: "FAILED",
          error_message: "crossref returned 504",
          finished_at: "2026-03-02T00:00:00Z",
        }),
      ],
      collectors: [],
    });
    expect(items).toHaveLength(1);
    expect(items[0]!.severity).toBe("ALERT");
    expect(items[0]!.title).toBe("Live matter");
    expect(items[0]!.reason).toContain("crossref returned 504");
    expect(items[0]!.href).toBe("/cases/l");
  });

  it("raises a queue no worker is serving, even though the job never failed", () => {
    const { items } = needsAttention({
      cases: [live],
      jobs: [
        makeJob({
          id: "j",
          case_id: "l",
          state: "QUEUED",
          effective_state: "PROCESSING_UNAVAILABLE",
          processing_available: false,
        }),
      ],
      collectors: [],
    });
    expect(items[0]!.severity).toBe("ALERT");
    expect(items[0]!.reason).toContain("no worker");
  });

  it("counts, rather than names, a job whose case was not loaded", () => {
    // Naming it would mean printing a raw uuid at a reader.
    const { items, unresolved } = needsAttention({
      cases: [live],
      jobs: [makeJob({ id: "j", case_id: "not-loaded", state: "FAILED" })],
      collectors: [],
    });
    expect(items).toHaveLength(0);
    expect(unresolved).toBe(1);
  });

  it("orders alerts above cautions", () => {
    const { items } = needsAttention({
      cases: [live, paused],
      jobs: [makeJob({ id: "j", case_id: "l", state: "FAILED" })],
      collectors: [
        makeCollector({ name: "search", available: false, unavailable_reason: "Not configured." }),
      ],
    });
    expect(items.map((item) => item.severity)).toEqual(["ALERT", "CAUTION", "CAUTION"]);
    expect(items[0]!.title).toBe("Live matter");
  });

  it("reports a collector's own reason rather than inventing one", () => {
    const { items } = needsAttention({
      cases: [],
      jobs: [],
      collectors: [
        makeCollector({
          name: "search",
          available: false,
          unavailable_reason: "No search provider is configured.",
        }),
      ],
    });
    expect(items[0]!.reason).toBe("No search provider is configured.");
    expect(items[0]!.href).toBe("/collectors");
  });

  it("says nothing at all when nothing is wrong", () => {
    const { items, unresolved } = needsAttention({
      cases: [live],
      jobs: [makeJob({ id: "j", case_id: "l", state: "COMPLETE" })],
      collectors: [makeCollector({ name: "dns_basic" })],
    });
    expect(items).toEqual([]);
    expect(unresolved).toBe(0);
  });
});

describe("recentCases", () => {
  it("returns the newest first, capped", () => {
    const rows = recentCases(
      [
        makeCase({ id: "1", name: "old", status: "NEW", created_at: "2026-01-01T00:00:00Z" }),
        makeCase({ id: "2", name: "new", status: "NEW", created_at: "2026-05-01T00:00:00Z" }),
        makeCase({ id: "3", name: "mid", status: "NEW", created_at: "2026-03-01T00:00:00Z" }),
      ],
      2,
    );
    expect(rows.map((item) => item.name)).toEqual(["new", "mid"]);
  });
});

describe("recentActivity", () => {
  const entry = (overrides: Partial<AuditEntry>): AuditEntry =>
    ({
      id: "a",
      occurred_at: "2026-04-01T10:00:00Z",
      actor_user_id: "u1",
      workspace_id: "w",
      event_type: "CASE_CREATED",
      object_type: "case",
      object_id: "c",
      request_id: "r",
      metadata: {},
      ...overrides,
    }) as AuditEntry;

  const member = {
    id: "m",
    user: { id: "u1", email: "a.mitchell@example.com", display_name: "A. Mitchell" },
    workspace_id: "w",
    role: "ADMIN",
    created_at: "2026-01-01T00:00:00Z",
  } as unknown as Membership;

  it("resolves the actor id to a name", () => {
    const line = recentActivity([entry({})], [member])[0]!;
    expect(line.who).toBe("A. Mitchell");
    expect(line.what).toBe("Case created");
  });

  it("never prints a raw uuid for an actor who has left", () => {
    const line = recentActivity([entry({ actor_user_id: "gone" })], [member])[0]!;
    expect(line.who).toBe("A former member");
    expect(line.who).not.toContain("gone");
  });

  it("says so when the log records no actor at all", () => {
    const line = recentActivity([entry({ actor_user_id: null })], [member])[0]!;
    expect(line.who).toBe("—");
  });
});
