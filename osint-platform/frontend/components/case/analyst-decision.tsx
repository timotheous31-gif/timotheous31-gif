"use client";

import { useState } from "react";

import { Badge, Button, Input } from "@/components/ui/primitives";
import { useSession } from "@/components/session";
import { api } from "@/lib/api";
import { PERMISSION, can, deniedMessage } from "@/lib/permissions";
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
  const { workspace } = useSession();
  // A VIEWER still *sees* the standing decision — it is part of the record — but
  // cannot change what the case concludes. POST /decisions enforces that.
  const mayDecide = can(workspace, PERMISSION.analystDecide);

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
      {mayDecide ? (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="rounded border border-line-strong px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:border-accent/60 hover:text-fg"
        >
          {open ? "Close" : "Review"}
        </button>
      ) : (
        <span className="text-[10px] text-faint" title={deniedMessage(workspace, "record a decision")}>
          read-only
        </span>
      )}

      {open ? (
        <div className="basis-full space-y-2 rounded border border-line bg-surface p-3">
          <label className="block">
            <span className="text-[10px] font-semibold uppercase tracking-label text-faint">
              Note (optional)
            </span>
            <Input
              aria-label="Analyst note"
              value={note}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setNote(event.target.value)}
              placeholder="Why you reached this conclusion"
              className="mt-1 text-xs"
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
            Your decision is recorded separately and does not change the correlation score the
            platform computed.
          </p>
          {error ? <p className="text-[11px] text-danger">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
