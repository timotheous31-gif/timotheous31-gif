"use client";

import { useState } from "react";

import { Button, ErrorNotice } from "@/components/ui/primitives";
import { api } from "@/lib/api";

/**
 * Destructive delete, with a confirmation that names the case.
 *
 * The dialog states plainly that deletion is permanent and lists what goes with
 * the case, because the counters on the overview page are the only warning an
 * investigator gets that a run's worth of work is about to disappear.
 *
 * A 409 is not an error to apologise for: it means a run is still active, and
 * the message the backend returns already says to cancel it first. It is shown
 * as-is rather than rewritten here, so the UI cannot drift from the rule the
 * API actually enforces.
 */
export function DeleteCase({
  caseId,
  caseName,
  onDeleted,
  variant = "menu",
}: {
  caseId: string;
  caseName: string;
  onDeleted: () => void;
  variant?: "menu" | "button";
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteCase(caseId);
      setOpen(false);
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
        aria-label={`Delete case ${caseName}`}
        className={
          variant === "menu"
            ? "rounded-md px-2 py-1 text-xs text-muted hover:bg-line hover:text-danger"
            : "rounded-md border border-line px-2 py-1 text-xs text-danger hover:bg-line"
        }
      >
        {variant === "menu" ? "⋯" : "Delete case"}
      </button>
    );
  }

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={`delete-case-${caseId}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="w-full max-w-md rounded-lg border border-line bg-panel p-4 shadow-lg">
        <h2 id={`delete-case-${caseId}`} className="text-sm font-semibold">
          Delete “{caseName}”?
        </h2>
        <p className="mt-2 text-xs text-muted">
          This permanently deletes the case and everything in it — targets, findings, entities,
          relationships, evidence, the timeline, collector runs and imported recon results.
        </p>
        <p className="mt-2 text-xs text-muted">
          This cannot be undone, and there is no export step. Tags are shared between cases and
          are kept.
        </p>
        {error ? (
          <div className="mt-3">
            <ErrorNotice error={error} />
          </div>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" onClick={confirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete permanently"}
          </Button>
        </div>
      </div>
    </div>
  );
}
