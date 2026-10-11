"use client";

import clsx from "clsx";

import { Badge, Eyebrow, Mono } from "@/components/ui/primitives";
import { reviewState } from "@/lib/case-workspace";
import type { PersonCandidate } from "@/lib/candidates";
import { formatConfidence, SCORE_LABEL } from "@/lib/format";

/**
 * One candidate, as an analyst review record.
 *
 * The card holds two things apart on purpose. The **correlation score** is
 * arithmetic: named rules, combined, uncalibrated against any measured
 * outcome — which is why it is a figure to two decimal places and never a
 * percentage. The **analyst decision** is a person's judgement, recorded
 * beside it and never folded into it. A reader has to be able to see which of
 * the two is making a given claim.
 *
 * The review state is carried four ways — a word, a glyph, the colour of the
 * vertical rail and the rail's weight — so it survives a greyscale print, a
 * colour-blind reader, and a screenshot pasted into a report.
 */
const RAIL: Record<string, string> = {
  CONFIRMED: "border-l-ok",
  RULED_OUT: "border-l-danger/70",
  AWAITING_REVIEW: "border-l-caution",
  AUTOMATED_ONLY: "border-l-edge",
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
        "relative border-l-2 px-5 py-4 transition-colors",
        RAIL[review.state],
        review.state === "RULED_OUT" && "opacity-75",
      )}
    >
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <Eyebrow>{candidate.sourceLabel}</Eyebrow>
          <h3 className="mt-1 text-[15px] font-semibold leading-tight tracking-tight text-fg">
            {candidate.name}
          </h3>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Badge tone={review.tone} glyph={review.glyph} title={review.meaning}>
            {review.label}
          </Badge>
          <span
            className="text-right"
            title={`${SCORE_LABEL}: named correlation rules combined. Not a probability.`}
          >
            <Mono className="readout block text-[17px] leading-none text-fg">
              {formatConfidence(candidate.confidence)}
            </Mono>
            <Eyebrow className="mt-1 !text-[9px]">{SCORE_LABEL}</Eyebrow>
          </span>
        </div>
      </div>

      {candidate.corroboratedBy.length > 0 || candidate.sharedIdentifiers.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {candidate.corroboratedBy.map((kind) => (
            <Badge
              key={kind}
              tone="OK"
              title="Something other than the name connects this record to what you supplied."
            >
              corroborated: {kind}
            </Badge>
          ))}
          {/* An identifier another index also carries, where independence could
              not be established. A separate badge from "corroborated" on
              purpose: one raised the score and the other deliberately did not. */}
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
          className="mt-2.5 block break-anywhere font-mono text-[11px] text-muted underline-offset-2 hover:text-accent hover:underline"
        >
          {candidate.url}
        </a>
      ) : null}

      {candidate.affiliations.length > 0 || candidate.locations.length > 0 ? (
        <p className="mt-2 text-meta text-muted">
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
            <p className="mt-3 border-l-2 border-l-rule-strong bg-telemetry px-3 py-2 text-meta text-muted">
              <span className="font-medium text-fg">Source-claimed citizenship:</span>{" "}
              {candidate.citizenshipClaims.join(", ")} — stated by {candidate.sourceLabel}. Not
              inferred, not a residence, not a current location, and it corroborates no country or
              city you supplied.
            </p>
          ) : null}

          <div className="mt-4 grid gap-x-6 gap-y-3 border-t border-rule-quiet pt-3 sm:grid-cols-2">
            <Reasons title="Supporting" tone="ok" reasons={candidate.matchReasons} />
            <Reasons title="Conflicting" tone="muted" reasons={candidate.mismatchReasons} />
          </div>

          {Object.keys(candidate.identifiers).length > 0 ? (
            <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-meta text-muted">
              {Object.entries(candidate.identifiers).map(([key, value]) => (
                <span key={key}>
                  <span className="eyebrow !inline">{key}</span>{" "}
                  <Mono className="text-fg">{value}</Mono>
                </span>
              ))}
            </p>
          ) : null}
        </>
      )}

      {candidate.decision ? (
        <p className="mt-3 border-t border-rule-quiet pt-2.5 text-meta text-muted">
          <span className="font-semibold text-fg">{review.label}</span>
          {candidate.decision.decided_by ? <> · {candidate.decision.decided_by}</> : null}
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
      <Eyebrow>{title}</Eyebrow>
      {reasons.length > 0 ? (
        <ul className="mt-1.5 space-y-1 text-meta">
          {reasons.map((reason) => (
            <li key={reason} className="flex gap-2">
              <span
                aria-hidden="true"
                className={clsx(
                  "mt-[7px] h-px w-2 shrink-0",
                  tone === "ok" ? "bg-ok" : "bg-weak",
                )}
              />
              <span className={tone === "ok" ? "text-fg" : "text-muted"}>{reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-meta text-faint">None recorded.</p>
      )}
    </div>
  );
}
