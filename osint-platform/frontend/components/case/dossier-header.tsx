"use client";

import Link from "next/link";

import { RunButton } from "@/components/case/run-button";
import { useSession } from "@/components/session";
import { Badge, Eyebrow, Mono, Surface } from "@/components/ui/primitives";
import { initialsOf, primarySubject, targetTypeLabel, type Portrait } from "@/lib/case-workspace";
import { formatDateTime, padCount } from "@/lib/format";
import type { Case, CaseSummary, Target } from "@/types/api";

/**
 * The dossier head.
 *
 * The surface an investigator reads first, and the one that has to say — before
 * any word is read — that this is a file on a subject rather than a record in a
 * table. It is the command material: deepest, lit from above, machined at one
 * corner, carrying the strongest type in the product.
 *
 * Everything on it is a stored value. There is no threat level, no wanted
 * status, no classification marking and no surveillance state, because the
 * platform holds none of those and printing one would be a lie with a serif.
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
  // user can only see one there is no ambiguity and we name it; when they can
  // see several, naming the selected one would be a guess dressed as a fact.
  const singleWorkspace = (session?.workspaces.length ?? 0) === 1;
  const workspaceName = singleWorkspace ? (workspace?.workspace.name ?? "—") : null;

  const subject = target?.raw_input ?? caseRecord.name;
  const kind = target ? targetTypeLabel(target.type) : "Case";

  return (
    <Surface material="command" notch className="overflow-hidden">
      <div className="flex flex-col gap-5 px-5 pb-4 pt-5 sm:flex-row sm:gap-6 sm:px-7 sm:pt-6">
        <SubjectPlate label={subject} portrait={portrait} />

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Link
                href="/cases"
                className="eyebrow inline-block transition-colors hover:text-accent"
              >
                ← All investigations
              </Link>
              <Eyebrow tone="accent" className="mt-3">
                {target ? `${kind} · Subject` : "Case file"}
              </Eyebrow>
              <h1 className="mt-1.5 truncate text-subject uppercase tracking-[0.01em] text-fg">
                {subject}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-2.5">
                <Badge tone={caseRecord.status}>{caseRecord.status}</Badge>
                {others > 0 ? (
                  <Link
                    href={`/cases/${caseId}/targets`}
                    className="text-[11px] text-muted transition-colors hover:text-accent"
                  >
                    +{others} other target{others === 1 ? "" : "s"}
                  </Link>
                ) : null}
              </div>
            </div>
            <div className="hidden shrink-0 sm:block">
              <RunButton caseId={caseId} onFinished={onRan} />
            </div>
          </div>

          {/* With no target the headline is already the case name, so repeating
              it here would be the header talking to itself. */}
          {target || caseRecord.description ? (
            <p className="mt-3.5 text-[13px] text-muted">
              {target ? <span className="text-fg">{caseRecord.name}</span> : null}
              {target && caseRecord.description ? " — " : null}
              {caseRecord.description}
            </p>
          ) : null}

          <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2.5 border-t border-rule-quiet pt-3.5">
            <Field label="Last run">
              {summary?.last_run_at ? (
                formatDateTime(summary.last_run_at)
              ) : (
                <span className="text-faint">Never</span>
              )}
            </Field>
            <Field label="Updated">{formatDateTime(caseRecord.updated_at)}</Field>
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
          </dl>

          <div className="mt-4 sm:hidden">
            <RunButton caseId={caseId} onFinished={onRan} />
          </div>
        </div>
      </div>

      <Holdings
        caseId={caseId}
        summary={summary}
        candidateCount={candidateCount}
        decisionCount={decisionCount}
      />
    </Surface>
  );
}

/**
 * The portrait plate.
 *
 * A rectangular crop in a thin inner frame with a brass corner marker, and a
 * provenance caption beneath it — the way a photograph is mounted in a file,
 * not the way an avatar sits in a web app. Saturation is pulled back so the
 * image reads as an artefact rather than a profile picture.
 *
 * A picture appears only when the platform fetched it and it is filed against
 * a candidate. The caption names that candidate, the page and the date.
 * Nothing here compares faces, draws a landmark, or asserts that the picture
 * is of the subject.
 */
function SubjectPlate({ label, portrait }: { label: string; portrait: Portrait | null }) {
  return (
    <figure className="m-0 w-[108px] shrink-0">
      <div className="relative">
        <div className="relative overflow-hidden rounded-sm border border-rule-strong bg-telemetry shadow-recessed">
          {portrait ? (
            <img
              src={portrait.imageUrl}
              alt={`Public image published on ${portrait.platformLabel}, filed against the candidate ${portrait.candidateName}`}
              width={108}
              height={128}
              className="h-32 w-[108px] object-cover [filter:saturate(0.72)_contrast(1.03)]"
            />
          ) : (
            <span
              aria-hidden="true"
              className="grid h-32 w-[108px] place-items-center font-mono text-2xl tracking-wide text-edge"
            >
              {initialsOf(label)}
            </span>
          )}
          {/* A hairline inset frame, as a print would be mounted. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-[3px] border border-white/[0.07]"
          />
        </div>
        {/* Corner marker: brass, editorial — this is a mounted artefact, not a
            live instrument, so it is not cyan. */}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -left-px -top-px h-3 w-3 border-l-2 border-t-2 border-brass/70"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-px -right-px h-3 w-3 border-b-2 border-r-2 border-brass/70"
        />
      </div>
      <figcaption className="mt-2 text-[10px] leading-snug text-faint">
        {portrait ? (
          <>
            <span className={portrait.confirmed ? "block text-ok" : "block text-muted"}>
              {portrait.confirmed ? "Confirmed candidate" : "Unconfirmed candidate"}
            </span>
            <a
              href={portrait.sourcePageUrl}
              target="_blank"
              rel="noreferrer noopener"
              title={`${portrait.sourcePageUrl}${
                portrait.retrievedAt
                  ? ` — retrieved ${formatDateTime(portrait.retrievedAt)}`
                  : ""
              }`}
              className="mt-0.5 block truncate font-mono text-muted underline-offset-2 hover:text-accent hover:underline"
            >
              {portrait.platformLabel}
            </a>
            {portrait.retrievedAt ? (
              <span className="block font-mono text-[9.5px] tabular-nums">
                {formatDateTime(portrait.retrievedAt).slice(0, 10)}
              </span>
            ) : null}
            <span
              className="mt-1 block"
              title="The platform stores this picture because a public page published it beside a name. Nothing here compares faces."
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
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-1 font-mono text-[12.5px] tabular-nums text-fg">{children}</dd>
    </div>
  );
}

/** What this file actually contains. */
function Holdings({
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
    <ul className="grid grid-cols-2 gap-px border-t border-rule-quiet bg-rule-quiet sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item) => (
        <li key={item.label} className="bg-command">
          <Link href={item.href} className="block px-5 py-3 transition-colors hover:bg-instrument/70">
            <Mono className="block text-[22px] leading-none tabular-nums tracking-tight text-fg">
              {item.value === null ? "—" : padCount(item.value)}
            </Mono>
            <Eyebrow className="mt-2">{item.label}</Eyebrow>
          </Link>
        </li>
      ))}
    </ul>
  );
}
