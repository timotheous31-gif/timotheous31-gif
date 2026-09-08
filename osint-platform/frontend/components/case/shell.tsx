"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext } from "react";

import { RunButton } from "@/components/case/run-button";
import { Badge, ErrorNotice, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { Case } from "@/types/api";

const CaseContext = createContext<string | null>(null);

/** The case id for the current route. */
export function useCaseId(): string {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCaseId must be used inside a case route");
  return value;
}

const TABS = [
  { segment: "", label: "Overview" },
  { segment: "targets", label: "Targets" },
  { segment: "findings", label: "Findings" },
  { segment: "recon", label: "Recon" },
  { segment: "candidates", label: "Candidates" },
  { segment: "social", label: "Social & images" },
  { segment: "entities", label: "Entities" },
  { segment: "graph", label: "Graph" },
  { segment: "timeline", label: "Timeline" },
  { segment: "evidence", label: "Evidence" },
  { segment: "report", label: "Report" },
];

export function CaseShell({ caseId, children }: { caseId: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const detail = useAsync<Case>(() => api.getCase(caseId), [caseId]);
  const base = `/cases/${caseId}`;

  return (
    <CaseContext.Provider value={caseId}>
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <Link href="/cases" className="text-xs text-muted hover:underline">
                ← All cases
              </Link>
              <h1 className="mt-1 truncate text-xl font-semibold">
                {detail.data?.name ?? (detail.loading ? "Loading…" : "Case")}
              </h1>
              {detail.data?.description ? (
                <p className="mt-1 text-sm text-muted">{detail.data.description}</p>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              {detail.data ? <Badge tone={detail.data.status}>{detail.data.status}</Badge> : null}
              <RunButton caseId={caseId} onFinished={detail.reload} />
            </div>
          </div>

          <nav className="flex flex-wrap gap-1 border-b border-line" aria-label="Case sections">
            {TABS.map((tab) => {
              const href = tab.segment ? `${base}/${tab.segment}` : base;
              const active = tab.segment
                ? pathname.startsWith(href)
                : pathname === base || pathname === `${base}/`;
              return (
                <Link
                  key={tab.label}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "-mb-px border-b-2 px-3 py-2 text-sm",
                    active
                      ? "border-accent font-medium text-accent"
                      : "border-transparent text-muted hover:text-fg",
                  )}
                >
                  {tab.label}
                </Link>
              );
            })}
          </nav>
        </header>

        {detail.error ? (
          <ErrorNotice error={detail.error} retry={detail.reload} />
        ) : detail.loading ? (
          <Spinner label="Loading case" />
        ) : (
          children
        )}
      </div>
    </CaseContext.Provider>
  );
}
