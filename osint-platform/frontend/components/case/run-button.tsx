"use client";

import { useState } from "react";

import { Badge, Button, ErrorNotice } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { displayState, stateHint, stateLabel, stateTone } from "@/lib/jobs";
import type { Job } from "@/types/api";

const TERMINAL = new Set(["COMPLETE", "FAILED", "CANCELLED"]);

/**
 * Starts an investigation and follows it to completion.
 *
 * Polling stops the moment the job reaches a terminal state, so an idle
 * dashboard makes no requests.
 */
export function RunButton({ caseId, onFinished }: { caseId: string; onFinished?: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const job = usePolling<Job>(
    () => api.getJob(jobId as string),
    (data) => !TERMINAL.has(data.state),
    2000,
    { enabled: Boolean(jobId) },
  );

  if (job.data && TERMINAL.has(job.data.state) && jobId) {
    // Fire once when the job settles, then stop tracking it.
    queueMicrotask(() => {
      setJobId(null);
      onFinished?.();
    });
  }

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const response = await api.runInvestigation(caseId, {});
      setJobId(response.job.id);
      if (TERMINAL.has(response.job.state)) {
        setJobId(null);
        onFinished?.();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!jobId) return;
    try {
      await api.cancelJob(jobId);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    }
  }

  const running = Boolean(jobId) && !TERMINAL.has(job.data?.state ?? "");
  const state = displayState(job.data);
  // A job that cannot start is not "0% Queued". Saying so is the difference
  // between an operator waiting indefinitely and one restarting the worker.
  const stalled = state === "PROCESSING_UNAVAILABLE";

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {running ? (
          <>
            <Badge tone={stateTone(state) ?? "RUNNING"} title={stateHint(state)}>
              {stalled
                ? stateLabel(state)
                : `${Math.round((job.data?.progress ?? 0) * 100)}% ${
                    job.data?.message ?? stateLabel(state)
                  }`}
            </Badge>
            <Button variant="danger" onClick={cancel}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={start} disabled={starting}>
            {starting ? "Starting…" : "Run investigation"}
          </Button>
        )}
      </div>
      {stalled ? (
        <p className="w-80 text-right text-xs text-danger">{stateHint(state)}</p>
      ) : null}
      {error ? (
        <div className="w-80">
          <ErrorNotice error={error} />
        </div>
      ) : null}
    </div>
  );
}
