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

export function bandLabel(band: MatchStrength): string {
  return band.replace(/_/g, " ").toLowerCase();
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
