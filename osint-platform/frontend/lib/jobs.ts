/**
 * Reading a job's state for display.
 *
 * The backend stores QUEUED even when nothing is consuming the queue, on
 * purpose: the row is a standing instruction and a worker that comes back must
 * still be able to run it. So "is anything able to act on this?" is a separate
 * question from "what state is it in", and it is answered here rather than by
 * rewriting the stored state.
 */

import type { EffectiveJobState, Job } from "@/types/api";

/** The state to display for `job`, or NOT_RUN when there is no job at all. */
export function displayState(job: Job | null | undefined): EffectiveJobState {
  if (!job) return "NOT_RUN";
  const effective = job.effective_state || job.state;
  return effective as EffectiveJobState;
}

/** Human label. Never says "Queued" when nothing can pick the job up. */
export function stateLabel(state: EffectiveJobState): string {
  const labels: Record<string, string> = {
    NOT_RUN: "Not run",
    QUEUED: "Queued",
    RUNNING: "Running",
    COMPLETE: "Complete",
    FAILED: "Failed",
    CANCELLED: "Cancelled",
    PROCESSING_UNAVAILABLE: "Processing unavailable",
  };
  return labels[state] ?? state;
}

/** One sentence explaining what the state means for the operator. */
export function stateHint(state: EffectiveJobState): string {
  const hints: Record<string, string> = {
    NOT_RUN: "This case has not been investigated yet.",
    QUEUED: "Waiting for a worker to pick it up.",
    RUNNING: "A worker is collecting now.",
    COMPLETE: "The run finished.",
    FAILED: "The run stopped on an error; the job records what happened.",
    CANCELLED: "Stopped at your request.",
    PROCESSING_UNAVAILABLE:
      "No worker is consuming the queue, so this job cannot start. It stays queued and " +
      "will run when a worker comes back — check that the celery-worker container is up.",
  };
  return hints[state] ?? "";
}

/** Badge tone, reusing the run-status vocabulary the rest of the UI uses. */
export function stateTone(state: EffectiveJobState): string | undefined {
  const tones: Record<string, string> = {
    RUNNING: "PARTIAL",
    COMPLETE: "SUCCESS",
    FAILED: "FAILED",
    CANCELLED: "SKIPPED",
    PROCESSING_UNAVAILABLE: "FAILED",
  };
  return tones[state];
}

/** True when the run is still expected to progress. */
export function isActive(state: EffectiveJobState): boolean {
  return state === "QUEUED" || state === "RUNNING";
}

/**
 * Whether the case can be deleted.
 *
 * Mirrors the backend's 409 rule rather than guessing: a job that is queued or
 * running blocks deletion. PROCESSING_UNAVAILABLE is still QUEUED underneath,
 * so it blocks too — the row is live even though nothing is serving it.
 */
export function canDelete(job: Job | null | undefined): boolean {
  if (!job) return true;
  return !isActive(job.state as EffectiveJobState);
}
