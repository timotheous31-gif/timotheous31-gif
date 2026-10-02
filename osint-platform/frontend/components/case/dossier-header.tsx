"use client";

import Link from "next/link";

import { RunButton } from "@/components/case/run-button";
import { useSession } from "@/components/session";
import { Badge } from "@/components/ui/primitives";
import {
  initialsOf,
  primarySubject,
  targetTypeLabel,
  type Portrait,
} from "@/lib/case-workspace";
import { formatDateTime } from "@/lib/format";
import type { Case, CaseSummary, Target } from "@/types/api";

/**
 * The strip an investigator reads first: who this case is about, what state it
 * is in, and what the platform holds on it.
 *
 * Everything here is a stored value. There is no risk level, no threat score
 * and no "priority" — the platform computes none of those.
 */
export function DossierHeader({
  caseId,
  caseRecord,
  summary,
  targets,
  portrait,
  candidateCount,
  decisionCount,
  onRan,
}: {
  caseId: string;
  caseRecord: Case;
  summary: CaseSummary | null;
  targets: Target[];
  portrait: Portrait | null;
  candidateCount: number | null;
  decisionCount: number | null;
  onRan: () => void;
}) {
  const { session, workspace } = useSession();
  const { target, others } = primarySubject(targets);

  // The API does not say which workspace a case belongs to. When the signed-in
  // user can only see one, there is no ambiguity and we name it; when they can
  // see several, naming the selected one would be a guess dressed as a fact.
  const singleWorkspace = (session?.workspaces.length ?? 0) === 1;
  const workspaceName = singleWorkspace ? (workspace?.workspace.name ?? "—") : null;

  return (
    <header className="rounded-lg border border-line bg-panel">
      <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start">
        <Subject target={target} portrait={portrait} />

        <div className="min-w-0 flex-1">
          <Link
            href="/cases"
            className="text-[11px] text-muted transition-colors hover:text-accent"
          >
            ← All cases
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-2.5">
            <h1 className="min-w-0 text-xl font-semibold leading-tight">{caseRecord.name}</h1>
            <Badge tone={caseRecord.status}>{caseRecord.status}</Badge>
          </div>
          {caseRecord.description ? (
            <p className="mt-1.5 max-w-3xl text-sm text-muted">{caseRecord.description}</p>
          ) : null}

          <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
            <Field label="Subject">
              {target ? (
                <>
                  {target.raw_input}
                  <span className="ml-1.5 text-faint">{targetTypeLabel(target.type)}</span>
                  {others > 0 ? (
                    <Link
                      href={`/cases/${caseId}/targets`}
                      className="ml-1.5 text-accent hover:underline"
                    >
                      +{others} more
                    </Link>
                  ) : null}
                </>
              ) : (
                <span className="text-faint">No target added</span>
              )}
            </Field>
            <Field
              label="Workspace"
              title={
                workspaceName
                  ? undefined
                  : "The API does not report which workspace a case belongs to, and you can see more than one."
              }
            >
              {workspaceName ?? <span className="text-faint">—</span>}
            </Field>
            <Field label="Last run">
              {summary?.last_run_at ? formatDateTime(summary.last_run_at) : (
                <span className="text-faint">Never</span>
              )}
            </Field>
            <Field label="Updated">{formatDateTime(caseRecord.updated_at)}</Field>
          </dl>
        </div>

        <div className="shrink-0">
          <RunButton caseId={caseId} onFinished={onRan} />
        </div>
      </div>

      <Counters
        caseId={caseId}
        summary={summary}
        candidateCount={candidateCount}
        decisionCount={decisionCount}
      />
    </header>
  );
}

/**
 * The portrait block.
 *
 * A picture appears only when the platform fetched it and it is filed against
 * a candidate, and its caption names that candidate, the page it came from and
 * when it was retrieved. Nothing here compares faces or asserts that the
 * picture is of the subject — it is a public image attached to a lead.
 */
function Subject({ target, portrait }: { target: Target | null; portrait: Portrait | null }) {
  const label = target?.raw_input ?? "?";
  return (
    <figure className="m-0 flex shrink-0 gap-3 sm:w-32 sm:flex-col sm:gap-2">
      {portrait ? (
        <img
          src={portrait.imageUrl}
          alt={`Public image published on ${portrait.platformLabel}, filed against the candidate ${portrait.candidateName}`}
          width={72}
          height={72}
          className="h-[72px] w-[72px] shrink-0 rounded border border-line-strong object-cover"
        />
      ) : (
        <span
          aria-hidden="true"
          className="grid h-[72px] w-[72px] shrink-0 place-items-center rounded border border-line bg-raised font-mono text-xl text-faint"
        >
          {initialsOf(label)}
        </span>
      )}
      <figcaption className="min-w-0 text-[10px] leading-snug text-faint">
        {portrait ? (
          <>
            <span className="block">
              <span className={portrait.confirmed ? "text-ok" : "text-muted"}>
                {portrait.confirmed ? "Confirmed candidate" : "Unconfirmed candidate"}
              </span>
            </span>
            <a
              href={portrait.sourcePageUrl}
              target="_blank"
              rel="noreferrer noopener"
              title={portrait.sourcePageUrl}
              className="block truncate text-muted underline-offset-2 hover:text-accent hover:underline"
            >
              {portrait.platformLabel}
              {portrait.retrievedAt ? ` · ${formatDateTime(portrait.retrievedAt).slice(0, 10)}` : ""}
            </a>
            <span
              className="mt-1 block"
              title="The platform stores this picture because a public page published it next to a name. Nothing here compares faces."
            >
              Public image. Not an identification.
            </span>
          </>
        ) : (
          <span className="block">No public image held against a candidate.</span>
        )}
      </figcaption>
    </figure>
  );
}

function Field({
  label,
  children,
  title,
}: {
  label: string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <div title={title}>
      <dt className="text-[10px] font-semibold uppercase tracking-label text-faint">{label}</dt>
      <dd className="mt-0.5 font-mono text-[12.5px] text-fg">{children}</dd>
    </div>
  );
}

/** The holdings line: what this case actually contains. */
function Counters({
  caseId,
  summary,
  candidateCount,
  decisionCount,
}: {
  caseId: string;
  summary: CaseSummary | null;
  candidateCount: number | null;
  decisionCount: number | null;
}) {
  const items: { label: string; value: number | null; href: string }[] = [
    { label: "Findings", value: summary?.findings ?? null, href: `/cases/${caseId}/findings` },
    { label: "Entities", value: summary?.entities ?? null, href: `/cases/${caseId}/entities` },
    { label: "Evidence", value: summary?.evidence ?? null, href: `/cases/${caseId}/evidence` },
    { label: "Candidates", value: candidateCount, href: `/cases/${caseId}/candidates` },
    { label: "Decisions", value: decisionCount, href: `/cases/${caseId}/candidates` },
  ];
  return (
    <ul className="grid grid-cols-2 border-t border-line sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item, index) => (
        <li
          key={item.label}
          className={index > 0 ? "border-line sm:border-l" : undefined}
        >
          <Link
            href={item.href}
            className="block px-4 py-2.5 transition-colors hover:bg-raised"
          >
            <span className="block text-[10px] font-semibold uppercase tracking-label text-faint">
              {item.label}
            </span>
            <span className="mt-0.5 block font-mono text-lg tabular-nums">
              {item.value ?? "—"}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
