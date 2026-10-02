/**
 * The one page header in the product.
 *
 * Before this, each top-level page wrote its own `<h1 className="text-xl…">`
 * and its own paragraph, which is how four pages ended up with four different
 * ideas of what a heading is. The header owns four slots and nothing else:
 *
 *   eyebrow   where you are, in small caps
 *   title     what this page is
 *   subtitle  one sentence, only when it earns its line
 *   meta      short factual readouts (counts, a timestamp, a workspace)
 *   actions   the controls that act on the whole page
 */

import type { ReactNode } from "react";

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
    <header className="border-b border-line pb-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="text-[10px] font-semibold uppercase tracking-label text-accent">
              {eyebrow}
            </p>
          ) : null}
          <h1 className="mt-1 truncate text-[22px] font-semibold leading-tight">{title}</h1>
          {subtitle ? <p className="mt-1.5 max-w-3xl text-sm text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {meta ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted">
          {meta}
        </div>
      ) : null}
    </header>
  );
}

/** One factual readout in a header's `meta` row. */
export function HeaderMeta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-label text-faint">{label}</span>
      <span className="font-mono text-[12.5px] text-fg tabular-nums">{value}</span>
    </span>
  );
}
