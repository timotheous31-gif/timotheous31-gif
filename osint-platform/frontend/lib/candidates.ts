/**
 * Reading PERSON candidates out of the entity graph.
 *
 * Candidates are PERSONA entities keyed on the public page they came from. The
 * shaping here is deliberately dumb — it reads what the backend recorded and
 * does no judging of its own — so the reasons an investigator sees are the ones
 * the collector actually wrote, not a second opinion invented in the browser.
 */

import type { CollectorRun, Entity } from "@/types/api";

/** Collector names that need no API key. Kept in step with the backend. */
export const FREE_PERSON_COLLECTORS = [
  "orcid",
  "openalex",
  "crossref",
  "wikidata",
  "github_people",
  "reddit",
  "person_usernames",
] as const;

export interface PersonCandidate {
  id: string;
  /** Name as the source spells it. */
  name: string;
  /** Collector that found it, e.g. "orcid". */
  source: string;
  /** Human label for that source, e.g. "ORCID". */
  sourceLabel: string;
  url: string;
  host: string;
  confidence: number;
  affiliations: string[];
  locations: string[];
  identifiers: Record<string, string>;
  /** Why this record may be the subject. */
  matchReasons: string[];
  /** Why it may not be — as important as the reasons for. */
  mismatchReasons: string[];
  /** Which supplied context corroborated it, if any. */
  corroboratedBy: string[];
  /**
   * Citizenships this source *states*, with the sentence that bounds them.
   *
   * Shown attributed and used for nothing. Not a location, not a residence, not
   * a current position, and never inferred — see the backend's
   * `CITIZENSHIP_CAVEAT`.
   */
  citizenshipClaims: string[];
  citizenshipNote: string | null;
  /**
   * Identifiers another index also carries, where independence could not be
   * established. Deliberately separate from `corroboratedBy`: the reader must
   * not have to guess which kind of claim a list is making.
   */
  sharedIdentifiers: SharedIdentifier[];
}

export interface SharedIdentifier {
  identifier: string;
  value: string;
  sources: string[];
  independence: string;
  label: string;
  reason: string;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function sharedIdentifiers(value: unknown): SharedIdentifier[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const entry = item as Record<string, unknown>;
    return [
      {
        identifier: String(entry["identifier"] ?? ""),
        value: String(entry["value"] ?? ""),
        sources: strings(entry["sources"]),
        independence: String(entry["independence"] ?? "UNKNOWN"),
        label: String(entry["label"] ?? ""),
        reason: String(entry["reason"] ?? ""),
      },
    ];
  });
}

function record(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item != null && item !== "")
      .map(([key, item]) => [key, String(item)]),
  );
}

/** True for the PERSONA entity representing the person under investigation. */
export function isSubject(entity: Entity): boolean {
  return entity.type === "PERSONA" && entity.canonical_value.startsWith("person:");
}

/** True for a candidate: one public record that carries the searched name. */
export function isCandidate(entity: Entity): boolean {
  return entity.type === "PERSONA" && entity.canonical_value.startsWith("person-candidate:");
}

export function toCandidate(entity: Entity): PersonCandidate {
  const attributes = entity.attributes ?? {};
  const source = String(attributes["source"] ?? "");
  return {
    id: entity.id,
    name: String(attributes["candidate_name"] ?? entity.display_name),
    source,
    sourceLabel: String(attributes["source_label"] ?? source ?? "Unknown source"),
    url: String(attributes["reference_url"] ?? ""),
    host: String(attributes["host"] ?? ""),
    confidence: entity.confidence,
    affiliations: strings(attributes["affiliations"]),
    locations: strings(attributes["locations"]),
    identifiers: record(attributes["identifiers"]),
    matchReasons: strings(attributes["match_reasons"]),
    mismatchReasons: strings(attributes["mismatch_reasons"]),
    corroboratedBy: strings(attributes["corroborated_by"]),
    citizenshipClaims: strings(attributes["citizenship_claims"]),
    citizenshipNote: attributes["citizenship_interpretation"]
      ? String(attributes["citizenship_interpretation"])
      : null,
    sharedIdentifiers: sharedIdentifiers(attributes["shared_identifiers"]),
  };
}

/**
 * Candidates most-corroborated first, then by confidence.
 *
 * Corroboration leads because it is the only thing that distinguishes one
 * same-name record from another; confidence alone would just re-sort ties.
 */
export function rankCandidates(entities: Entity[]): PersonCandidate[] {
  return entities
    .filter(isCandidate)
    .map(toCandidate)
    .sort(
      (a, b) =>
        b.corroboratedBy.length - a.corroboratedBy.length || b.confidence - a.confidence,
    );
}

export function groupBySource(candidates: PersonCandidate[]): Map<string, PersonCandidate[]> {
  const grouped = new Map<string, PersonCandidate[]>();
  for (const candidate of candidates) {
    const key = candidate.sourceLabel || candidate.source || "Unknown source";
    grouped.set(key, [...(grouped.get(key) ?? []), candidate]);
  }
  return grouped;
}

export interface SourceRun {
  collector: string;
  status: CollectorRun["status"];
  free: boolean;
  /** Why a run was skipped or failed, shown verbatim. */
  reason: string | null;
  candidates: number;
}

/**
 * One row per collector run, so the page can say which free sources actually
 * ran — and, just as importantly, which did not and why.
 */
export function summariseRuns(runs: CollectorRun[], candidates: PersonCandidate[]): SourceRun[] {
  const counts = new Map<string, number>();
  for (const candidate of candidates) {
    counts.set(candidate.source, (counts.get(candidate.source) ?? 0) + 1);
  }
  return runs
    .map((run) => ({
      collector: run.collector,
      status: run.status,
      free: (FREE_PERSON_COLLECTORS as readonly string[]).includes(run.collector),
      reason: run.error_message,
      candidates: counts.get(run.collector) ?? 0,
    }))
    .sort((a, b) => Number(b.free) - Number(a.free) || a.collector.localeCompare(b.collector));
}
