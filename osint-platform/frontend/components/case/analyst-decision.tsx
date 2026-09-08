"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { DECISIONS, decisionLabel, decisionTone } from "@/lib/social";
import type { AnalystDecisionRecord, AnalystDecisionValue, DecisionSubject } from "@/types/api";

/**
 * Record what the analyst concluded about one association.
 *
 * Sits beside the automated confidence rather than replacing it. Choosing a
 * decision writes only to the decisions table — the score the platform computed
 * is untouched, and both are shown.
 */
export function AnalystDecisionControl({
  caseId,
  subjectType,
  subjectId,
  current,
  onChanged,
}: {
  caseId: string;
  subjectType: DecisionSubject;
  subjectId: string;
  current: AnalystDecisionRecord | null;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(current?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(decision: AnalystDecisionValue) {
    setBusy(true);
    setError(null);
    try {
      await api.recordDecision(caseId, {
        subject_type: subjectType,
        subject_id: subjectId,
        decision,
        note: note.trim() || null,
      });
      setOpen(false);
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    setBusy(true);
    setError(null);
    try {
      await api.clearDecision(caseId, subjectType, subjectId);
      setOpen(false);
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone={decisionTone(current?.decision)} title={current?.note ?? undefined}>
        Analyst: {decisionLabel(current?.decision)}
      </Badge>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted hover:bg-line"
      >
        {open ? "Close" : "Review"}
      </button>

      {open ? (
        <div className="basis-full space-y-1.5 rounded-md border border-line p-2">
          <label className="block text-[11px]">
            <span className="text-muted">Note (optional)</span>
            <input
              aria-label="Analyst note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Why you reached this conclusion"
              className="mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-xs"
            />
          </label>
          <div className="flex flex-wrap gap-1.5">
            {DECISIONS.map((decision) => (
              <Button key={decision} onClick={() => choose(decision)} disabled={busy}>
                {decisionLabel(decision)}
              </Button>
            ))}
            {current ? (
              <Button variant="danger" onClick={withdraw} disabled={busy}>
                Withdraw
              </Button>
            ) : null}
          </div>
          <p className="text-[11px] text-muted">
            Your decision is recorded separately and does not change the confidence the
            platform computed.
          </p>
          {error ? <p className="text-[11px] text-danger">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
