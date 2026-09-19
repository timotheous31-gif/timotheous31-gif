"use client";

import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import {
  partitionCandidates,
  rankCandidates,
  summariseRuns,
  withPlacements,
  type PersonCandidate,
} from "@/lib/candidates";
import { formatConfidence } from "@/lib/format";

/**
 * Person candidates and the reasoning behind each one.
 *
 * The page is built around a single idea: a name search returns strangers, and
 * the investigator's job is to rule them out. So every candidate shows the
 * reasons against it as prominently as the reasons for it, and the sources that
 * produced nothing are listed too — an absent source is a gap in coverage, not
 * a negative result.
 */
export default function CandidatesPage() {
  const caseId = useCaseId();
  const entities = useAsync(
    () => api.listEntities(caseId, { type: "PERSONA", limit: 500 }),
    [caseId],
  );
  const runs = useAsync(() => api.listRuns(caseId), [caseId]);
  // The placements — and the analyst decisions behind them — are computed by
  // the backend and read here. The browser groups; it does not judge.
  const groups = useAsync(() => api.listCandidateGroups(caseId), [caseId]);

  const candidates = withPlacements(rankCandidates(entities.data?.items ?? []), groups.data);
  const { primary, lowConfidence, rejected } = partitionCandidates(candidates);
  // Coverage counts every candidate a source produced, suppressed or not: what
  // a source found is a fact about the source, not about the presentation.
  const sources = summariseRuns(runs.data ?? [], candidates);
  const freeSources = sources.filter((source) => source.free);
  const paidSources = sources.filter((source) => !source.free);
  const loading = entities.loading || runs.loading;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Free sources"
          description="Public sources that need no API key, and what each returned"
        />
        {loading ? (
          <Spinner />
        ) : freeSources.length === 0 ? (
          <div className="p-4">
            <Empty
              title="No free source has run yet"
              hint="Add a PERSON target and run the investigation."
            />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Source</Th>
                <Th>Status</Th>
                <Th>Candidates</Th>
                <Th>Note</Th>
              </tr>
            </thead>
            <tbody>
              {freeSources.map((source) => (
                <tr key={source.collector}>
                  <Td>
                    <Mono>{source.collector}</Mono>{" "}
                    <Badge tone="SUCCESS">free</Badge>
                  </Td>
                  <Td>
                    <Badge tone={source.status}>{source.status}</Badge>
                  </Td>
                  <Td>{source.candidates}</Td>
                  <Td className="max-w-md text-xs text-muted">{source.reason ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {paidSources.length > 0 ? (
          <p className="border-t border-line px-4 py-3 text-xs text-muted">
            Optional paid sources:{" "}
            {paidSources.map((source) => (
              <span key={source.collector} className="mr-2">
                <Mono>{source.collector}</Mono> ({source.status})
              </span>
            ))}
            — investigations do not depend on these.
          </p>
        ) : null}
      </Card>

      {entities.error ? <ErrorNotice error={entities.error} retry={entities.reload} /> : null}

      <Card>
        <CardHeader
          title="Candidates"
          description="Each is a public record carrying the name — a question, not an identification"
        />
        {loading ? (
          <Spinner />
        ) : candidates.length === 0 ? (
          <div className="p-4">
            <Empty
              title="No candidates yet"
              hint="Run the investigation, or add context to narrow an existing one."
            />
          </div>
        ) : primary.length === 0 ? (
          <div className="p-4">
            <Empty
              title="Nothing is corroborated yet"
              hint="Every candidate found so far matches on the name alone. They are below; supplying a username, organisation, ORCID or city is what lets one be corroborated."
            />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {primary.map((candidate) => (
              <li key={candidate.id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{candidate.name}</span>
                  <Badge>{candidate.sourceLabel}</Badge>
                  <Badge tone={candidate.corroboratedBy.length > 0 ? "SUCCESS" : "SKIPPED"}>
                    {formatConfidence(candidate.confidence)}
                  </Badge>
                  {candidate.corroboratedBy.map((kind) => (
                    <Badge key={kind} tone="SUCCESS">
                      corroborated: {kind}
                    </Badge>
                  ))}
                  {/*
                    An identifier another index also carries, where independence
                    could not be established. A separate badge from
                    "corroborated" on purpose: one raised the score and the other
                    deliberately did not.
                  */}
                  {candidate.sharedIdentifiers.map((shared) => (
                    <Badge
                      key={`${shared.identifier}:${shared.value}`}
                      tone="PARTIAL"
                      title={shared.reason}
                    >
                      shared {shared.identifier} · {shared.independence.toLowerCase()} lineage ·
                      no score effect
                    </Badge>
                  ))}
                </div>

                {candidate.citizenshipClaims.length > 0 ? (
                  <p className="text-xs text-muted">
                    <span className="font-medium text-fg">Source-claimed citizenship:</span>{" "}
                    {candidate.citizenshipClaims.join(", ")}{" "}
                    <span>
                      — stated by {candidate.sourceLabel}. Not inferred, not a residence, not a
                      current location, and it corroborates no country or city you supplied.
                    </span>
                  </p>
                ) : null}

                {candidate.url ? (
                  <a
                    href={candidate.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="block text-xs text-muted underline"
                  >
                    {candidate.url}
                  </a>
                ) : null}

                {candidate.affiliations.length > 0 || candidate.locations.length > 0 ? (
                  <p className="text-xs text-muted">
                    {candidate.affiliations.length > 0
                      ? `Affiliations: ${candidate.affiliations.join(", ")}`
                      : null}
                    {candidate.affiliations.length > 0 && candidate.locations.length > 0
                      ? " · "
                      : null}
                    {candidate.locations.length > 0
                      ? `Places: ${candidate.locations.join(", ")}`
                      : null}
                  </p>
                ) : null}

                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                      Why this may be them
                    </h3>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
                      {candidate.matchReasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                      Why it may not
                    </h3>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted">
                      {candidate.mismatchReasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                </div>

                {Object.keys(candidate.identifiers).length > 0 ? (
                  <p className="text-xs text-muted">
                    {Object.entries(candidate.identifiers).map(([key, value]) => (
                      <span key={key} className="mr-3">
                        {key}: <Mono>{value}</Mono>
                      </span>
                    ))}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <SuppressedCandidates
        title="Low-confidence candidates"
        hint="These carry the searched name, or part of it, and nothing else that connects them to the subject. Nothing has been deleted: each is stored, scored and exported exactly as any other candidate."
        candidates={lowConfidence}
      />
      <SuppressedCandidates
        title="Ruled out by an analyst"
        hint="Kept for the audit trail. A decision is a human judgement recorded beside the automated score, never merged into it."
        candidates={rejected}
      />

      <Card>
        <CardHeader title="How to read this page" />
        <ul className="list-disc space-y-1 p-4 pl-8 text-sm text-muted">
          <li>
            Every candidate is a separate record. Two candidates sharing a name are never
            treated as the same person without independent evidence.
          </li>
          <li>
            A name match alone is capped well below the threshold at which this platform would
            ever merge two entities, so nothing here is an identification.
          </li>
          <li>
            Supplying known usernames, profile URLs, organisations, schools or a city on the
            target is what lets a candidate be corroborated — or ruled out.
          </li>
          <li>A source that returned nothing is a gap in coverage, not a negative result.</li>
        </ul>
      </Card>
    </div>
  );
}

/**
 * A collapsed group of candidates the default view sets aside.
 *
 * Closed by default, with the count in the summary: the point is that an
 * analyst can see at a glance how much was folded away, and open it in one
 * click. A count of zero renders nothing rather than an empty section.
 *
 * Every candidate in here is still in the database, still scored, still carries
 * its evidence and still appears in the exported report. This is where it is
 * drawn, not whether it exists.
 */
function SuppressedCandidates({
  title,
  hint,
  candidates,
}: {
  title: string;
  hint: string;
  candidates: PersonCandidate[];
}) {
  if (candidates.length === 0) return null;
  return (
    <Card>
      <details>
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">
          {title} ({candidates.length})
          <span className="ml-2 font-normal text-muted">— click to review</span>
        </summary>
        <p className="px-4 pb-3 text-xs text-muted">{hint}</p>
        <ul className="divide-y divide-line border-t border-line">
          {candidates.map((candidate) => (
            <li key={candidate.id} className="space-y-1 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{candidate.name}</span>
                <Badge>{candidate.sourceLabel}</Badge>
                <Badge tone="SKIPPED">{formatConfidence(candidate.confidence)}</Badge>
              </div>
              {candidate.presentationReason ? (
                <p className="text-xs text-muted">{candidate.presentationReason}</p>
              ) : null}
              {candidate.url ? (
                <a
                  href={candidate.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block text-xs text-muted underline"
                >
                  {candidate.url}
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      </details>
    </Card>
  );
}
