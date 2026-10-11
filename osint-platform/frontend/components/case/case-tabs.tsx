"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import type { TabCounts } from "@/lib/case-workspace";

/**
 * The case's sections.
 *
 * Module navigation: moving between intelligence tools, not switching panes.
 *
 * Links, not ARIA tabs. Each one is a route with its own URL that an
 * investigator will bookmark and paste into a report, and dressing routes up
 * as `role="tab"` would promise arrow-key semantics the browser's history
 * does not have. So: a labelled `<nav>`, `aria-current` on the active link,
 * and the native tab order — which is also what a screen reader expects from
 * something that changes the address bar.
 *
 * No pills. Compact uppercase labels, a subdued count, and a single lit
 * underline on the active module.
 *
 * Counts appear only where the platform has given us one. `undefined` draws no
 * badge at all, because an empty section and a section whose count has not
 * loaded are different claims.
 */
const TABS: { segment: string; label: string; count?: keyof TabCounts }[] = [
  { segment: "", label: "Overview" },
  { segment: "targets", label: "Targets", count: "targets" },
  { segment: "findings", label: "Findings", count: "findings" },
  { segment: "recon", label: "Recon" },
  { segment: "candidates", label: "Candidates", count: "candidates" },
  { segment: "social", label: "Social & images" },
  { segment: "entities", label: "Entities", count: "entities" },
  { segment: "graph", label: "Graph" },
  { segment: "timeline", label: "Timeline", count: "timeline" },
  { segment: "evidence", label: "Evidence", count: "evidence" },
  { segment: "report", label: "Report" },
];

export function CaseTabs({ caseId, counts }: { caseId: string; counts: TabCounts }) {
  const pathname = usePathname();
  const base = `/cases/${caseId}`;
  const strip = useRef<HTMLDivElement>(null);

  // On a narrow screen the active section can start off-screen. Bring it into
  // view horizontally only — scrolling the page itself would yank the reader
  // away from the header they just arrived at.
  //
  // Re-run when the counts land, not only when the route changes: the badges
  // widen every tab, which pushes the active one back out of view a beat after
  // the first pass put it there.
  const shape = JSON.stringify(counts);
  useEffect(() => {
    const active = strip.current?.querySelector('[aria-current="page"]');
    active?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname, shape]);

  return (
    <nav aria-label="Case sections" className="relative">
      <div
        ref={strip}
        className="flex gap-4 overflow-x-auto border-b border-rule pb-px [scrollbar-width:none] [&::-webkit-scrollbar]:hidden xl:gap-5"
      >
        {TABS.map((tab) => {
          const href = tab.segment ? `${base}/${tab.segment}` : base;
          const active = tab.segment
            ? pathname.startsWith(href)
            : pathname === base || pathname === `${base}/`;
          const count = tab.count ? counts[tab.count] : undefined;
          return (
            <Link
              key={tab.label}
              href={href}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "relative flex shrink-0 items-baseline gap-1.5 whitespace-nowrap py-2.5",
                "text-[11px] font-semibold uppercase tracking-[0.1em] transition-colors",
                active ? "text-accent" : "text-faint hover:text-fg",
              )}
            >
              {tab.label}
              {count !== undefined ? (
                <span
                  className={clsx(
                    "font-mono text-[10px] tabular-nums",
                    active ? "text-accent" : "text-faint",
                  )}
                >
                  {count}
                </span>
              ) : null}
              {/* The active module is marked by a lit underline, not a pill:
                  a row of filled capsules reads as a toolbar. */}
              {active ? (
                <span
                  aria-hidden="true"
                  className="absolute inset-x-0 -bottom-px h-0.5 bg-accent shadow-[0_0_10px_0_rgb(var(--accent)/0.7)]"
                />
              ) : null}
            </Link>
          );
        })}
      </div>
      {/* A hint that the strip continues past the edge, where it does. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-bg to-transparent"
      />
    </nav>
  );
}
