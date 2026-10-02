/**
 * What the case workspace is allowed to say about a case.
 *
 * Same rule as the overview dashboard: every value here is read from rows the
 * platform already stores, and nothing is scored, weighted or extrapolated.
 * In particular there is no risk score, no threat level and no "readiness"
 * grade — the platform computes none of those, and a confident-looking number
 * nobody computed is worse than no number.
 *
 * It lives in `lib/` because the repository's test runner has no DOM, so logic
 * that is testable has to be logic that is not a component.
 */

import { isCandidate } from "@/lib/candidates";
import { displayState, stateLabel } from "@/lib/jobs";
import type {
  AnalystDecisionValue,
  CandidateGroup,
  Case,
  CaseSummary,
  CollectorRun,
  Entity,
  GraphResponse,
  ImageEvidenceRecord,
  Job,
  Target,
  TargetType,
  TimelineEvent,
} from "@/types/api";

// --- the subject ------------------------------------------------------------

export interface PrimarySubject {
  target: Target | null;
  /** How many other targets the case carries, so the header can say so. */
  others: number;
}

/**
 * The target the header leads with.
 *
 * A PERSON first, because a person case is about a person and the rest of the
 * targets are usually context for them; otherwise whichever target was added
 * first. This is a presentation choice and the header says how many others
 * there are — the API has no notion of a primary target, so inventing one
 * silently would misrepresent the case.
 */
export function primarySubject(targets: Target[]): PrimarySubject {
  if (targets.length === 0) return { target: null, others: 0 };
  const ordered = [...targets].sort((left, right) =>
    left.created_at.localeCompare(right.created_at),
  );
  const person = ordered.find((item) => item.type === "PERSON");
  return { target: person ?? ordered[0]!, others: ordered.length - 1 };
}

export function hasPersonTarget(targets: Target[]): boolean {
  return targets.some((item) => item.type === "PERSON");
}

/** Up to two initials, for the placeholder where no portrait can be shown. */
export function initialsOf(value: string): string {
  return (
    value
      .split(/[\s._@-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?"
  );
}

export interface Portrait {
  imageUrl: string;
  sourcePageUrl: string;
  platformLabel: string;
  retrievedAt: string | null;
  /** The candidate the picture is filed against — never "the subject". */
  candidateName: string;
  confirmed: boolean;
}

/**
 * The one public picture worth showing beside the subject.
 *
 * Mirrors the rule the dossier renderer already applies (`profile_image_for`),
 * deliberately rather than inventing a second one: an image the platform
 * actually fetched, filed against a candidate, preferring one an analyst
 * confirmed and then the strongest-scoring candidate.
 *
 * Two things this is not. It is not an identification — the caption names the
 * candidate and the page it came from, and nothing here compares faces or
 * claims the picture is of the subject. And it is never an image the platform
 * did not read: rendering a URL we never fetched would make the investigator's
 * browser request it from a page nobody vetted, and would look like verified
 * evidence when it is not.
 */
export function portraitFor({
  targets,
  images,
  entities,
}: {
  targets: Target[];
  images: ImageEvidenceRecord[];
  entities: Entity[];
}): Portrait | null {
  if (!hasPersonTarget(targets)) return null;

  const candidates = new Map(
    entities.filter(isCandidate).map((entity) => [entity.id, entity]),
  );
  const usable = images.filter(
    (image) =>
      image.fetch_state === "FETCHED" &&
      Boolean(image.final_url || image.image_url) &&
      image.candidate_entity_id !== null &&
      candidates.has(image.candidate_entity_id),
  );
  if (usable.length === 0) return null;

  const rank = (image: ImageEvidenceRecord): [number, number] => {
    const candidate = candidates.get(image.candidate_entity_id!);
    const confirmed = image.decision?.decision === "CONFIRMED";
    return [confirmed ? 0 : 1, -(candidate?.confidence ?? 0)];
  };
  const best = [...usable].sort((left, right) => {
    const [la, lb] = rank(left);
    const [ra, rb] = rank(right);
    return la - ra || lb - rb;
  })[0]!;
  const candidate = candidates.get(best.candidate_entity_id!);

  return {
    imageUrl: best.final_url || best.image_url,
    sourcePageUrl: best.source_page_url,
    platformLabel: best.platform || "public page",
    retrievedAt: best.retrieved_at,
    candidateName: String(
      candidate?.attributes?.["candidate_name"] ?? candidate?.display_name ?? "a candidate",
    ),
    confirmed: best.decision?.decision === "CONFIRMED",
  };
}

// --- the operational strip --------------------------------------------------

export type CellTone = "OK" | "CAUTION" | "ALERT" | "INFO" | "NEUTRAL";

export interface StatusCell {
  key: string;
  label: string;
  value: string;
  /** One short sentence saying what the value means. Never a score. */
  detail: string;
  tone: CellTone;
  href?: string;
}

const CASE_STATUS_TONE: Record<Case["status"], CellTone> = {
  RUNNING: "INFO",
  NEW: "NEUTRAL",
  PAUSED: "CAUTION",
  COMPLETE: "OK",
  ARCHIVED: "NEUTRAL",
};

export interface Coverage {
  ran: number;
  succeeded: number;
  failed: number;
  skipped: number;
  partial: number;
}

/** What the collectors actually did. A source that did not run is a gap. */
export function coverage(runs: CollectorRun[]): Coverage {
  const count = (status: CollectorRun["status"]) =>
    runs.filter((run) => run.status === status).length;
  return {
    ran: runs.length,
    succeeded: count("SUCCESS"),
    failed: count("FAILED") + count("TIMEOUT"),
    skipped: count("SKIPPED"),
    partial: count("PARTIAL"),
  };
}

export interface CandidatePosture {
  total: number;
  /** Shown in the main list: corroborated, above threshold, or confirmed. */
  primary: number;
  /** Name-only and weak. Kept and counted, never deleted. */
  lowConfidence: number;
  confirmed: number;
  rejected: number;
  /** No decision recorded, or recorded as needing one. */
  unresolved: number;
}

export function candidatePosture(groups: CandidateGroup[]): CandidatePosture {
  const posture: CandidatePosture = {
    total: groups.length,
    primary: 0,
    lowConfidence: 0,
    confirmed: 0,
    rejected: 0,
    unresolved: 0,
  };
  for (const group of groups) {
    if (group.presentation === "LOW_CONFIDENCE") posture.lowConfidence += 1;
    else if (group.presentation !== "REJECTED") posture.primary += 1;

    const decision = group.decision?.decision;
    if (decision === "CONFIRMED") posture.confirmed += 1;
    else if (decision === "REJECTED") posture.rejected += 1;
    else posture.unresolved += 1;
  }
  return posture;
}

/** Decisions an analyst has actually recorded, newest first. */
export function recordedDecisions(
  groups: CandidateGroup[],
): { key: string; subject: string; decision: AnalystDecisionValue; note: string | null; by: string | null; at: string }[] {
  return groups
    .filter((group) => group.decision)
    .map((group) => ({
      key: group.decision!.id,
      subject: group.display_name,
      decision: group.decision!.decision,
      note: group.decision!.note,
      by: group.decision!.decided_by,
      at: group.decision!.decided_at,
    }))
    .sort((left, right) => right.at.localeCompare(left.at));
}

/** The job that last told us anything, newest first by creation. */
export function latestJob(jobs: Job[]): Job | null {
  if (jobs.length === 0) return null;
  return [...jobs].sort((left, right) => right.created_at.localeCompare(left.created_at))[0]!;
}

/**
 * The six cells of the operational strip.
 *
 * Tone is applied only where a state is genuinely bad or genuinely live. A
 * count that is merely a count stays neutral, so a coloured cell on this strip
 * always carries information.
 */
export function statusCells({
  caseRecord,
  summary,
  runs,
  jobs,
  groups,
  caseId,
}: {
  caseRecord: Case;
  summary: CaseSummary | null;
  runs: CollectorRun[];
  jobs: Job[];
  groups: CandidateGroup[];
  caseId: string;
}): StatusCell[] {
  const cover = coverage(runs);
  const posture = candidatePosture(groups);
  const job = latestJob(jobs);
  const state = displayState(job);

  const runTone: CellTone =
    state === "FAILED" || state === "PROCESSING_UNAVAILABLE"
      ? "ALERT"
      : state === "RUNNING" || state === "QUEUED"
        ? "INFO"
        : state === "COMPLETE"
          ? "OK"
          : "NEUTRAL";

  const coverageTone: CellTone =
    cover.ran === 0
      ? "NEUTRAL"
      : cover.failed > 0
        ? "ALERT"
        : cover.skipped > 0 || cover.partial > 0
          ? "CAUTION"
          : "OK";

  const findings = summary?.findings ?? 0;
  const evidence = summary?.evidence ?? 0;

  return [
    {
      key: "investigation",
      label: "Investigation",
      value: caseRecord.status,
      detail: "The status recorded on the case.",
      tone: CASE_STATUS_TONE[caseRecord.status] ?? "NEUTRAL",
    },
    {
      key: "run",
      label: "Latest run",
      value: stateLabel(state),
      detail: job?.message ?? "No run has been started for this case.",
      tone: runTone,
      href: `/cases/${caseId}`,
    },
    {
      key: "coverage",
      label: "Source coverage",
      value: cover.ran === 0 ? "None" : `${cover.succeeded}/${cover.ran}`,
      detail:
        cover.ran === 0
          ? "No collector has run yet."
          : `${cover.succeeded} of ${cover.ran} runs succeeded` +
            (cover.partial ? `, ${cover.partial} partial` : "") +
            (cover.skipped ? `, ${cover.skipped} skipped` : "") +
            (cover.failed ? `, ${cover.failed} failed` : "") +
            ".",
      tone: coverageTone,
    },
    {
      key: "unresolved",
      label: "Unresolved",
      value: posture.total === 0 ? "—" : String(posture.unresolved),
      detail:
        posture.total === 0
          ? "No candidates have been produced."
          : `${posture.unresolved} of ${posture.total} candidates have no analyst decision.`,
      tone: posture.total === 0 ? "NEUTRAL" : posture.unresolved > 0 ? "CAUTION" : "OK",
      href: `/cases/${caseId}/candidates`,
    },
    {
      key: "evidence",
      label: "Evidence",
      value: String(evidence),
      detail:
        evidence === 0
          ? "Nothing has been stored yet."
          : "Stored artefacts, each hashed at collection.",
      tone: "NEUTRAL",
      href: `/cases/${caseId}/evidence`,
    },
    {
      // Deliberately "Report", not "report readiness". Nothing in the platform
      // gates a report or grades one; what can be said honestly is what the
      // report would currently be built from.
      key: "report",
      label: "Report",
      value: findings === 0 ? "Empty" : `${findings} findings`,
      detail:
        findings === 0
          ? "A report would have nothing to describe until a collector runs."
          : `Built from ${findings} findings and ${evidence} stored artefacts.`,
      tone: "NEUTRAL",
      href: `/cases/${caseId}/report`,
    },
  ];
}

// --- snapshots --------------------------------------------------------------

/** The most recent timeline events, newest first. */
export function timelinePreview(events: TimelineEvent[], limit = 4): TimelineEvent[] {
  return [...events]
    .sort((left, right) => right.occurred_at.localeCompare(left.occurred_at))
    .slice(0, limit);
}

export interface GraphSnapshot {
  nodes: number;
  edges: number;
  components: number;
  types: [string, number][];
  mostConnected: GraphResponse["summary"]["most_connected"];
}

/**
 * The shape of the graph, or nothing.
 *
 * Returns null when there are no relationships at all, so the page can show an
 * honest empty state rather than drawing a network that does not exist.
 */
export function graphSnapshot(graph: GraphResponse | null | undefined): GraphSnapshot | null {
  if (!graph || graph.summary.edge_count === 0) return null;
  return {
    nodes: graph.summary.node_count,
    edges: graph.summary.edge_count,
    components: graph.summary.component_count,
    types: Object.entries(graph.summary.entity_type_counts).sort((a, b) => b[1] - a[1]),
    mostConnected: graph.summary.most_connected.slice(0, 4),
  };
}

// --- tabs -------------------------------------------------------------------

export interface TabCounts {
  targets?: number;
  findings?: number;
  candidates?: number;
  entities?: number;
  timeline?: number;
  evidence?: number;
}

/**
 * Counts for the tab row — only where a real count exists.
 *
 * `undefined` means "the platform has not told us", which the tab renders as no
 * badge at all. Zero is a fact and is shown; a missing number is not drawn as
 * zero, because an empty tab and an unloaded tab are different things.
 */
export function tabCounts(
  summary: CaseSummary | null,
  groups: CandidateGroup[] | null,
): TabCounts {
  return {
    targets: summary?.targets,
    findings: summary?.findings,
    candidates: groups?.length,
    entities: summary?.entities,
    timeline: summary?.timeline_events,
    evidence: summary?.evidence,
  };
}

/** What a PERSON/DOMAIN/… target type is called on screen. */
export function targetTypeLabel(type: TargetType): string {
  const labels: Partial<Record<TargetType, string>> = {
    PERSON: "Person",
    ORGANIZATION: "Organisation",
    DOMAIN: "Domain",
    EMAIL: "Email",
    USERNAME: "Username",
    URL: "URL",
    IP: "IP address",
    REPOSITORY: "Repository",
    SOCIAL_PROFILE: "Social profile",
  };
  return labels[type] ?? type;
}

/** The context an investigator supplied with a PERSON target, as label/value. */
export function suppliedContext(target: Target | null): { label: string; values: string[] }[] {
  if (!target) return [];
  const context = target.attributes?.["context"];
  if (!context || typeof context !== "object") return [];
  const labels: Record<string, string> = {
    affiliations: "Affiliations",
    known_usernames: "Known usernames",
    locations: "Locations",
    schools: "Schools",
    occupations: "Occupations",
    websites: "Websites",
  };
  return Object.entries(context as Record<string, unknown>)
    .filter(([, value]) => Array.isArray(value) && value.length > 0)
    .map(([key, value]) => ({
      label: labels[key] ?? key.replace(/_/g, " "),
      values: (value as unknown[]).map(String),
    }));
}

// --- candidate review state -------------------------------------------------

export type ReviewState = "CONFIRMED" | "RULED_OUT" | "AWAITING_REVIEW" | "AUTOMATED_ONLY";

export interface ReviewBadge {
  state: ReviewState;
  label: string;
  /** So the state survives without colour. */
  glyph: string;
  tone: string;
  /** What this state actually means, for the title attribute. */
  meaning: string;
}

const REVIEW: Record<ReviewState, ReviewBadge> = {
  CONFIRMED: {
    state: "CONFIRMED",
    label: "Confirmed",
    glyph: "✓",
    tone: "OK",
    meaning: "An analyst recorded that this record is the subject.",
  },
  RULED_OUT: {
    state: "RULED_OUT",
    label: "Ruled out",
    glyph: "✕",
    tone: "ALERT",
    meaning: "An analyst recorded that this record is not the subject.",
  },
  AWAITING_REVIEW: {
    state: "AWAITING_REVIEW",
    label: "Awaiting review",
    glyph: "◐",
    tone: "CAUTION",
    meaning: "An analyst looked at this and did not reach a conclusion.",
  },
  AUTOMATED_ONLY: {
    state: "AUTOMATED_ONLY",
    label: "Automated only",
    glyph: "○",
    tone: "NEUTRAL",
    meaning: "No analyst has recorded a judgement. Only the correlation rules have run.",
  },
};

/**
 * How a candidate stands with the analyst — never with the scoring engine.
 *
 * The distinction that matters here is between a candidate nobody has looked at
 * and one a person looked at and could not settle. Both are "undecided" to a
 * counter, and they mean completely different things to an investigator.
 */
export function reviewState(decision: AnalystDecisionValue | null | undefined): ReviewBadge {
  if (decision === "CONFIRMED") return REVIEW.CONFIRMED;
  if (decision === "REJECTED") return REVIEW.RULED_OUT;
  if (decision === "UNRESOLVED" || decision === "NEEDS_REVIEW") return REVIEW.AWAITING_REVIEW;
  return REVIEW.AUTOMATED_ONLY;
}
