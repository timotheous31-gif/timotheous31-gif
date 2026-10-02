"use client";

import clsx from "clsx";
import Link from "next/link";

import type { CellTone, StatusCell } from "@/lib/case-workspace";

/**
 * Six operational readouts, side by side.
 *
 * Tone is spent only where a state is genuinely bad or genuinely live, so a
 * coloured cell here always carries information; a count that is merely a
 * count stays neutral. Each cell also carries a word — "Failed", "Running",
 * "3/8" — so none of this depends on seeing the colour.
 */
const TONE: Record<CellTone, { value: string; rail: string }> = {
  OK: { value: "text-ok", rail: "bg-ok" },
  CAUTION: { value: "text-caution", rail: "bg-caution" },
  ALERT: { value: "text-danger", rail: "bg-danger" },
  INFO: { value: "text-info", rail: "bg-info" },
  NEUTRAL: { value: "text-fg", rail: "bg-line-strong" },
};

export function StatusStrip({ cells }: { cells: StatusCell[] }) {
  return (
    <ul className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
      {cells.map((cell) => {
        const tone = TONE[cell.tone];
        const body = (
          <>
            <span aria-hidden="true" className={clsx("block h-0.5 w-6 rounded", tone.rail)} />
            <span className="mt-2 block text-[10px] font-semibold uppercase tracking-label text-faint">
              {cell.label}
            </span>
            <span className={clsx("mt-1 block font-mono text-[15px] leading-tight", tone.value)}>
              {cell.value}
            </span>
            <span className="mt-1.5 block text-[11px] leading-snug text-muted">{cell.detail}</span>
          </>
        );
        return (
          <li key={cell.key} className="bg-panel">
            {cell.href ? (
              <Link href={cell.href} className="block h-full px-3 py-3 transition-colors hover:bg-raised">
                {body}
              </Link>
            ) : (
              <div className="h-full px-3 py-3">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
