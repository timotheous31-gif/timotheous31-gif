"use client";

import clsx from "clsx";

import { Badge, Mono } from "@/components/ui/primitives";
import { reviewState } from "@/lib/case-workspace";
import type { PersonCandidate } from "@/lib/candidates";
import { formatConfidence, SCORE_LABEL } from "@/lib/format";

/**
 * One candidate, and the whole argument about it.
 *
 * The card holds two separate things apart on purpose. The **correlation
 * score** is arithmetic: named rules, combined, uncalibrated against any
 * measured outcome — which is why it is a number to two decimal places and
 * never a percentage. The **analyst decision** is a person's judgement,
 * recorded beside it and never folded into it. A reader has to be able to see
 * which of the two is making a given claim.
 *
 * The review state is carried three ways — a word, a glyph and the weight of
 * the left rail — so it survives a greyscale print, a colour-blind reader and a
 * screenshot pasted into a report.
 */
const RAIL: Record<string, string> = {
  CONFIRMED: "border-l-ok",
  RULED_OUT: "border-l-danger",
  AWAITING_REVIEW: "border-l-caution",
  AUTOMATED_ONLY: "border-l-line-strong",
};

export function CandidateCard({
  candidate,
  action,
  compact = false,
}: {
  candidate: PersonCandidate;
  /** The analyst-decision control, when the page offers one. */
  action?: React.ReactNode;
  compact?: boolean;
}) {
  const review = reviewState(candidate.decision?.decision);

  return (
    <article
      className={clsx(
        "border-l-2 bg-panel px-4 py-3.5 transition-colors",
        RAIL[review.state],
        review.state === "RULED_OUT" && "opacity-80",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{candidate.name}</h3>
        <Badge tone={review.tone} glyph={review.glyph} title={review.meaning}>
          {review.label}
        </Badge>
        <Badge tone="NEUTRAL" title={`Source: ${candidate.sourceLabel}`}>
          {candidate.sourceLabel}
        </Badge>
        <span
          className="ml-auto shrink-0 font-mono text-[13px] tabular-nums text-fg"
          title={`${SCORE_LABEL}: named correlation rules combined. Not a probability.`}
        >
          <span className="mr-1.5 text-[10px] uppercase tracking-label text-faint">
            {SCORE_LABEL}
          </span>
          {formatConfidence(candidate.confidence)}
        </span>
      </div>

      {candidate.corroboratedBy.length > 0 || candidate.sharedIdentifiers.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {candidate.corroboratedBy.map((kind) => (
            <Badge key={kind} tone="OK" title="Something other than the name connects this record to what you supplied.">
              corroborated: {kind}
            </Badge>
          ))}
          {/*
            An identifier another index also carries, where independence could
            not be established. A separate badge from "corroborated" on purpose:
            one raised the score and the other deliberately did not.
          */}
          {candidate.sharedIdentifiers.map((shared) => (
            <Badge
              key={`${shared.identifier}:${shared.value}`}
              tone="CAUTION"
              title={shared.reason}
            >
              shared {shared.identifier} · {shared.independence.toLowerCase()} lineage · no score
              effect
            </Badge>
          ))}
        </div>
      ) : null}

      {candidate.url ? (
        <a
          href={candidate.url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-2 block break-anywhere font-mono text-[11.5px] text-muted underline-offset-2 hover:text-accent hover:underline"
        >
          {candidate.url}
        </a>
      ) : null}

      {candidate.affiliations.length > 0 || candidate.locations.length > 0 ? (
        <p className="mt-2 text-xs text-muted">
          {candidate.affiliations.length > 0
            ? `Affiliations: ${candidate.affiliations.join(", ")}`
            : null}
          {candidate.affiliations.length > 0 && candidate.locations.length > 0 ? " · " : null}
          {candidate.locations.length > 0 ? `Places: ${candidate.locations.join(", ")}` : null}
        </p>
      ) : null}

      {compact ? null : (
        <>
          {candidate.citizenshipClaims.length > 0 ? (
            <p className="mt-2 rounded border border-line bg-surface px-3 py-2 text-xs text-muted">
              <span className="font-medium text-fg">Source-claimed citizenship:</span>{" "}
              {candidate.citizenshipClaims.join(", ")} — stated by {candidate.sourceLabel}. Not
              inferred, not a residence, not a current location, and it corroborates no country or
              city you supplied.
            </p>
          ) : null}

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Reasons title="Why this may be them" tone="ok" reasons={candidate.matchReasons} />
            <Reasons title="Why it may not" tone="muted" reasons={candidate.mismatchReasons} />
          </div>

          {Object.keys(candidate.identifiers).length > 0 ? (
            <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              {Object.entries(candidate.identifiers).map(([key, value]) => (
                <span key={key}>
                  {key}: <Mono>{value}</Mono>
                </span>
              ))}
            </p>
          ) : null}
        </>
      )}

      {candidate.decision ? (
        <p className="mt-3 border-t border-line pt-2.5 text-xs text-muted">
          <span className="font-medium text-fg">{review.label}</span>
          {candidate.decision.decided_by ? <> by {candidate.decision.decided_by}</> : null}
          {candidate.decision.note ? <> — “{candidate.decision.note}”</> : null}
        </p>
      ) : null}

      {action ? <div className="mt-3">{action}</div> : null}
    </article>
  );
}

function Reasons({
  title,
  tone,
  reasons,
}: {
  title: string;
  tone: "ok" | "muted";
  reasons: string[];
}) {
  return (
    <div>
      <h4 className="text-[10px] font-semibold uppercase tracking-label text-faint">{title}</h4>
      {reasons.length > 0 ? (
        <ul className="mt-1 space-y-1 text-xs">
          {reasons.map((reason) => (
            <li key={reason} className="flex gap-1.5">
              <span
                aria-hidden="true"
                className={clsx("mt-1.5 h-1 w-1 shrink-0 rounded-full", tone === "ok" ? "bg-ok" : "bg-weak")}
              />
              <span className={tone === "ok" ? "text-fg" : "text-muted"}>{reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-xs text-faint">None recorded.</p>
      )}
    </div>
  );
}
