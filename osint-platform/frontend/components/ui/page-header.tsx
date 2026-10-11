/**
 * The one page header for the top-level sections.
 *
 * Not a card: a title block over a strong horizontal rule, the way a dossier
 * opens a section. Four slots and nothing else — an eyebrow saying where you
 * are, the title, one optional sentence, a row of factual readouts, and the
 * controls that act on the whole page.
 */

import type { ReactNode } from "react";

import { Eyebrow } from "@/components/ui/primitives";

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  meta,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="relative pb-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow ? <Eyebrow tone="accent">{eyebrow}</Eyebrow> : null}
          <h1 className="mt-2 truncate text-display text-fg">{title}</h1>
          {subtitle ? (
            <p className="mt-2 max-w-2xl text-lede text-muted">{subtitle}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {meta ? (
        <div className="mt-4 flex flex-wrap items-center gap-x-7 gap-y-2">{meta}</div>
      ) : null}
      {/* The rule carries a short lit run at its head, so a section opens
          rather than merely ending. */}
      <span
        aria-hidden="true"
        className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-accent/50 via-rule to-transparent"
      />
    </header>
  );
}

/** One factual readout in a header's `meta` row. */
export function HeaderMeta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-2">
      <span className="eyebrow">{label}</span>
      <span className="font-mono text-[12.5px] tabular-nums text-fg">{value}</span>
    </span>
  );
}
