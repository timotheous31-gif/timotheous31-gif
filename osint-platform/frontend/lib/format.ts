/** Presentation helpers shared across the dashboard. */

import type { Classification, MatchStrength } from "@/types/api";

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 10);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toISOString().slice(0, 10)} ${date.toISOString().slice(11, 16)} UTC`;
}

export function formatConfidence(value: number): string {
  return value.toFixed(2);
}

/** The band a score falls into. Mirrors the backend's documented thresholds. */
export function confidenceBand(score: number): MatchStrength {
  if (score >= 0.9) return "LIKELY_MATCH";
  if (score >= 0.7) return "PROBABLE_MATCH";
  if (score >= 0.5) return "POSSIBLE_MATCH";
  return "WEAK_ASSOCIATION";
}

/**
 * What a band is called where a person reads it.
 *
 * The stored values still say `PROBABLE_MATCH` — renaming a column and an API
 * contract is its own change — but "probable match" on a 0.70 invites reading the
 * score as a probability, and it is not one. The vocabulary a reader sees says
 * correlation, and identity stays an analyst decision.
 */
const CORRELATION_BANDS: Record<MatchStrength, string> = {
  LIKELY_MATCH: "Strong correlation",
  PROBABLE_MATCH: "Moderate correlation",
  POSSIBLE_MATCH: "Weak correlation",
  WEAK_ASSOCIATION: "Name-level only",
};

export function bandLabel(band: MatchStrength): string {
  return CORRELATION_BANDS[band] ?? band.replace(/_/g, " ");
}

export function humanise(value: string): string {
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^./, (char) => char.toUpperCase());
}

export function truncate(value: string, length = 80): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Classifications that mean "handle with care" in the UI. */
export const RESTRICTIVE_CLASSIFICATIONS: Classification[] = ["SENSITIVE", "RESTRICTED"];
