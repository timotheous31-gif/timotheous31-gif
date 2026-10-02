"use client";

import { CandidateCard } from "@/components/case/candidate-card";
import { useCaseWorkspace } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  Mono,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import {
  partitionCandidates,
  rankCandidates,
  summariseRuns,
  withPlacements,
  type PersonCandidate,
} from "@/lib/candidates";
import { reviewState } from "@/lib/case-workspace";
import { formatConfidence } from "@/lib/format";

/**
 * Person candidates and the reasoning behind each one.
 *
 * The page is built around a single idea: a name search returns strangers, and
 * the investigator's job is to rule them out. So every candidate shows the
 * reasons against it as prominently as the reasons for it, and the sources that
 * produced nothing are listed too — an absent source is a gap in coverage, not
 * a negative result.
 *
 * The entities, runs and placements all come from the shell, which already
 * loaded them for the header. Before, this page refetched the runs and the
 * placements that the overview had just fetched.
 */
export default function CandidatesPage() {
  const { runs, groups, personas, loading } = useCaseWorkspace();

  const candidates = withPlacements(rankCandidates(personas), groups);
  const { primary, lowConfidence, rejected } = partitionCandidates(candidates);
  // Coverage counts every candidate a source produced, suppressed or not: what
  // a source found is a fact about the source, not about the presentation.
  const sources = summariseRuns(runs, candidates);
  const freeSources = sources.filter((source) => source.free);
  const paidSources = sources.filter((source) => !source.free);

  return (
    <div className="space-y-4">
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
                    <Mono>{source.collector}</Mono>
                  </Td>
                  <Td>
                    <Badge tone={source.status}>{source.status}</Badge>
                  </Td>
                  <Td className="font-mono tabular-nums">{source.candidates}</Td>
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

      <Card>
        <CardHeader
          title="Candidates"
          description="Each is a public record carrying the name — a question, not an identification"
          action={<Legend />}
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
              <li key={candidate.id}>
                <CandidateCard candidate={candidate} />
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
 * What the four review states mean.
 *
 * Spelled out because the difference between "nobody has looked at this" and
 * "someone looked and could not settle it" is the difference between work to do
 * and work already done, and a colour cannot say that.
 */
function Legend() {
  const states = (["CONFIRMED", "RULED_OUT", "AWAITING_REVIEW", "AUTOMATED_ONLY"] as const).map(
    (state) =>
      reviewState(
        state === "CONFIRMED"
          ? "CONFIRMED"
          : state === "RULED_OUT"
            ? "REJECTED"
            : state === "AWAITING_REVIEW"
              ? "NEEDS_REVIEW"
              : null,
      ),
  );
  return (
    <div className="flex flex-wrap gap-1.5">
      {states.map((review) => (
        <Badge key={review.state} tone={review.tone} glyph={review.glyph} title={review.meaning}>
          {review.label}
        </Badge>
      ))}
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
        <summary className="cursor-pointer px-4 py-3 text-[13px] font-semibold uppercase tracking-label marker:text-faint">
          {title}
          <span className="ml-2 font-mono text-xs normal-case tracking-normal text-accent">
            {candidates.length}
          </span>
          <span className="ml-2 font-normal normal-case tracking-normal text-muted">
            — click to review
          </span>
        </summary>
        <p className="px-4 pb-3 text-xs text-muted">{hint}</p>
        <ul className="divide-y divide-line border-t border-line">
          {candidates.map((candidate) => (
            <li key={candidate.id} className="space-y-1.5 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{candidate.name}</span>
                <Badge tone="NEUTRAL">{candidate.sourceLabel}</Badge>
                <span className="ml-auto font-mono text-[12.5px] tabular-nums text-muted">
                  {formatConfidence(candidate.confidence)}
                </span>
              </div>
              {candidate.presentationReason ? (
                <p className="text-xs text-muted">{candidate.presentationReason}</p>
              ) : null}
              {candidate.url ? (
                <a
                  href={candidate.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block break-anywhere font-mono text-[11px] text-muted underline-offset-2 hover:text-accent hover:underline"
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
