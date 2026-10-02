"use client";

import { createContext, useContext } from "react";

import { CaseTabs } from "@/components/case/case-tabs";
import { DossierHeader } from "@/components/case/dossier-header";
import { StatusStrip } from "@/components/case/status-strip";
import { ErrorNotice, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import {
  hasPersonTarget,
  portraitFor,
  statusCells,
  tabCounts,
  type Portrait,
} from "@/lib/case-workspace";
import type {
  CandidateGroup,
  Case,
  CaseSummary,
  CollectorRun,
  Entity,
  Job,
  Target,
} from "@/types/api";

/**
 * The case workspace: one header, one status strip, one tab row, and the
 * section the route asked for.
 *
 * The shell loads the case's shared state *once* and hands it to every section
 * through context. The header and the strip need all of it on every tab
 * anyway, and before this the Overview and Candidates pages each fetched the
 * collector runs separately — so this is fewer requests per visit, not more,
 * and the header and the page can no longer disagree about what the case
 * contains.
 */
export interface CaseWorkspace {
  caseId: string;
  detail: Case | null;
  summary: CaseSummary | null;
  targets: Target[];
  runs: CollectorRun[];
  jobs: Job[];
  groups: CandidateGroup[];
  personas: Entity[];
  portrait: Portrait | null;
  loading: boolean;
  /** Re-reads everything the shell owns. Sections call it after they change something. */
  reload: () => void;
}

const WorkspaceContext = createContext<CaseWorkspace | null>(null);

/** The case id for the current route. */
export function useCaseId(): string {
  return useCaseWorkspace().caseId;
}

/** Everything the shell already loaded for this case. */
export function useCaseWorkspace(): CaseWorkspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useCaseWorkspace must be used inside a case route");
  return value;
}

export function CaseShell({ caseId, children }: { caseId: string; children: React.ReactNode }) {
  const detail = useAsync<Case>(() => api.getCase(caseId), [caseId]);
  const summary = useAsync(() => api.caseSummary(caseId), [caseId]);
  const targets = useAsync(() => api.listTargets(caseId, { limit: 100 }), [caseId]);
  const runs = useAsync(() => api.listRuns(caseId), [caseId]);
  const jobs = useAsync(() => api.listCaseJobs(caseId), [caseId]);
  const groups = useAsync(() => api.listCandidateGroups(caseId), [caseId]);
  const personas = useAsync(
    () => api.listEntities(caseId, { type: "PERSONA", limit: 500 }),
    [caseId],
  );

  const targetList = targets.data?.items ?? [];
  // Only a PERSON case can have a subject portrait, so a domain case never pays
  // for the request.
  const isPerson = hasPersonTarget(targetList);
  const images = useAsync(() => api.listImages(caseId), [caseId, isPerson], {
    enabled: isPerson,
  });

  const reload = () => {
    detail.reload();
    summary.reload();
    targets.reload();
    runs.reload();
    jobs.reload();
    groups.reload();
    personas.reload();
    images.reload();
  };

  const personaList = personas.data?.items ?? [];
  const groupList = groups.data ?? [];
  const workspace: CaseWorkspace = {
    caseId,
    detail: detail.data,
    summary: summary.data,
    targets: targetList,
    runs: runs.data ?? [],
    jobs: jobs.data ?? [],
    groups: groupList,
    personas: personaList,
    portrait: portraitFor({
      targets: targetList,
      images: images.data ?? [],
      entities: personaList,
    }),
    loading: detail.loading || summary.loading || targets.loading,
    reload,
  };

  if (detail.error) return <ErrorNotice error={detail.error} retry={detail.reload} />;
  if (!detail.data) return <Spinner label="Loading case" />;

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="mx-auto max-w-6xl space-y-4">
        <DossierHeader
          caseId={caseId}
          caseRecord={detail.data}
          summary={summary.data}
          targets={targetList}
          portrait={workspace.portrait}
          candidateCount={groups.data ? groupList.length : null}
          decisionCount={
            groups.data ? groupList.filter((group) => group.decision).length : null
          }
          onRan={reload}
        />

        <StatusStrip
          cells={statusCells({
            caseRecord: detail.data,
            summary: summary.data,
            runs: workspace.runs,
            jobs: workspace.jobs,
            groups: groupList,
            caseId,
          })}
        />

        <CaseTabs caseId={caseId} counts={tabCounts(summary.data, groups.data)} />

        {children}
      </div>
    </WorkspaceContext.Provider>
  );
}
