import { describe, expect, it } from "vitest";

import { canDelete, displayState, isActive, stateHint, stateLabel, stateTone } from "@/lib/jobs";
import type { Job } from "@/types/api";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "1", case_id: "c1", celery_id: null, state: "QUEUED", progress: 0,
    message: null, started_at: null, finished_at: null, error_type: null,
    error_message: null, params: {}, result: {}, cancel_requested: false,
    created_at: "2026-01-01T00:00:00Z", effective_state: "QUEUED",
    processing_available: true,
    ...overrides,
  };
}

describe("displayState", () => {
  it("says NOT_RUN when the case has never been run", () => {
    expect(displayState(null)).toBe("NOT_RUN");
    expect(displayState(undefined)).toBe("NOT_RUN");
  });

  it("prefers the backend's effective state over the stored one", () => {
    expect(
      displayState(job({ state: "QUEUED", effective_state: "PROCESSING_UNAVAILABLE" })),
    ).toBe("PROCESSING_UNAVAILABLE");
  });

  it("falls back to the stored state when the field is absent", () => {
    expect(displayState(job({ state: "RUNNING", effective_state: "" }))).toBe("RUNNING");
  });
});

describe("stateLabel", () => {
  it("never shows Queued for a job nothing can pick up", () => {
    expect(stateLabel("PROCESSING_UNAVAILABLE")).toMatch(/unavailable/i);
    expect(stateLabel("PROCESSING_UNAVAILABLE")).not.toMatch(/queued/i);
  });

  it("labels every state it is given", () => {
    for (const state of ["NOT_RUN", "QUEUED", "RUNNING", "COMPLETE", "FAILED", "CANCELLED"] as const) {
      expect(stateLabel(state)).toBeTruthy();
    }
  });

  it("falls back to the raw state rather than inventing one", () => {
    expect(stateLabel("SOMETHING_NEW" as never)).toBe("SOMETHING_NEW");
  });
});

describe("stateHint", () => {
  it("tells the operator what to actually check when nothing is consuming", () => {
    const hint = stateHint("PROCESSING_UNAVAILABLE");
    expect(hint).toMatch(/celery-worker/);
    expect(hint).toMatch(/will run when a worker comes back/i);
  });

  it("does not claim a queued job is stuck when a worker exists", () => {
    expect(stateHint("QUEUED")).not.toMatch(/unavailable/i);
  });
});

describe("isActive / stateTone", () => {
  it("treats queued and running as active", () => {
    expect(isActive("QUEUED")).toBe(true);
    expect(isActive("RUNNING")).toBe(true);
    expect(isActive("COMPLETE")).toBe(false);
    expect(isActive("NOT_RUN")).toBe(false);
  });

  it("marks an unavailable queue as a problem, not a success", () => {
    expect(stateTone("PROCESSING_UNAVAILABLE")).toBe("FAILED");
    expect(stateTone("COMPLETE")).toBe("SUCCESS");
  });
});

describe("canDelete", () => {
  it("allows deleting a case that was never run", () => {
    expect(canDelete(null)).toBe(true);
  });

  it.each(["COMPLETE", "FAILED", "CANCELLED"] as const)("allows deleting after %s", (state) => {
    expect(canDelete(job({ state, effective_state: state }))).toBe(true);
  });

  it.each(["QUEUED", "RUNNING"] as const)("blocks deletion while %s, matching the 409", (state) => {
    expect(canDelete(job({ state, effective_state: state }))).toBe(false);
  });

  it("still blocks when the queue is unavailable, because the row is live", () => {
    expect(
      canDelete(job({ state: "QUEUED", effective_state: "PROCESSING_UNAVAILABLE" })),
    ).toBe(false);
  });
});
