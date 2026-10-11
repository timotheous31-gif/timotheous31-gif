"use client";

import clsx from "clsx";
import Link from "next/link";

import { Eyebrow } from "@/components/ui/primitives";
import type { CellTone, StatusCell } from "@/lib/case-workspace";

/**
 * Case instrumentation.
 *
 * One continuous recessed strip with hairline separators — not six cards. Each
 * cell is a label, a readout and a short tinted underline that carries the
 * state; the tint is spent only where a state is genuinely bad or genuinely
 * live, so a coloured rule here always means something. Every cell also says
 * its state in words, so none of it depends on seeing the colour.
 */
const TONE: Record<CellTone, { value: string; rule: string }> = {
  OK: { value: "text-ok", rule: "bg-ok" },
  CAUTION: { value: "text-caution", rule: "bg-caution" },
  ALERT: { value: "text-danger", rule: "bg-danger" },
  INFO: { value: "text-accent", rule: "bg-accent shadow-[0_0_8px_0_rgb(var(--accent)/0.65)]" },
  NEUTRAL: { value: "text-fg", rule: "bg-edge" },
};

export function StatusStrip({ cells }: { cells: StatusCell[] }) {
  return (
    <ul className="grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-rule-quiet shadow-recessed sm:grid-cols-3 lg:grid-cols-6">
      {cells.map((cell) => {
        const tone = TONE[cell.tone];
        const body = (
          <>
            <Eyebrow>{cell.label}</Eyebrow>
            <span
              className={clsx(
                "readout mt-1.5 block truncate font-mono text-[15px] uppercase",
                tone.value,
              )}
            >
              {cell.value}
            </span>
            <span aria-hidden="true" className={clsx("mt-2 block h-[2px] w-10 rounded-sm", tone.rule)} />
            <span className="mt-2 block text-[11px] leading-snug text-muted">{cell.detail}</span>
          </>
        );
        return (
          <li key={cell.key} className="bg-telemetry">
            {cell.href ? (
              <Link
                href={cell.href}
                className="block h-full px-4 py-3.5 transition-colors hover:bg-instrument/60"
              >
                {body}
              </Link>
            ) : (
              <div className="h-full px-4 py-3.5">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
