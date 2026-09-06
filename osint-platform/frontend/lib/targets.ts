/**
 * Target-entry rules shared by the Add Target form.
 *
 * These live outside the component so the decision the form makes — "must the
 * investigator classify this before it can be submitted?" — is testable on its
 * own, without a DOM.
 */

import type { NormalizationPreview, PersonContext, TargetType } from "@/types/api";

/** Types an investigator can state explicitly. `""` means "infer from the shape". */
export const SELECTABLE_TYPES: readonly TargetType[] = [
  "PERSON",
  "ORGANIZATION",
  "DOMAIN",
  "USERNAME",
  "EMAIL",
  "URL",
  "IP",
  "REPOSITORY",
  "SOCIAL_PROFILE",
];

/** What each ambiguous choice means, shown beside the buttons. */
export const TYPE_HELP: Partial<Record<TargetType, string>> = {
  PERSON: "A named individual. Only name-based public search runs; no infrastructure lookups.",
  ORGANIZATION: "A company, institution or group.",
};

/**
 * True when the form must collect a type before it can submit.
 *
 * The backend refuses to guess between PERSON and ORGANIZATION for a bare name,
 * so an ambiguous preview with no choice made is not submittable. Once the
 * investigator has chosen — from the dropdown or the prompt — it is.
 */
export function requiresExplicitType(
  preview: NormalizationPreview | null,
  chosenType: TargetType | "",
): boolean {
  return preview?.ambiguous === true && chosenType === "";
}

/** Whether "Add target" should be clickable. */
export function canSubmitTarget(
  value: string,
  preview: NormalizationPreview | null,
  chosenType: TargetType | "",
  busy: boolean,
): boolean {
  return !busy && value.trim().length > 0 && !requiresExplicitType(preview, chosenType);
}

/**
 * The types offered in the disambiguation prompt.
 *
 * Driven by the backend's `candidates` so the UI cannot drift from what the API
 * will actually accept; the literal is only a fallback for an older backend.
 */
export function ambiguityChoices(preview: NormalizationPreview | null): TargetType[] {
  if (!preview?.ambiguous) return [];
  return preview.candidates.length > 0 ? preview.candidates : ["PERSON", "ORGANIZATION"];
}

/** The raw text fields the Add Target form collects for a PERSON. */
export interface PersonContextInput {
  knownUsernames: string;
  profileUrls: string;
  organizations: string;
  schools: string;
  country: string;
  city: string;
}

export const EMPTY_PERSON_CONTEXT: PersonContextInput = {
  knownUsernames: "",
  profileUrls: "",
  organizations: "",
  schools: "",
  country: "",
  city: "",
};

function list(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Turn the form's comma-separated text into a PersonContext, or null when the
 * investigator supplied nothing.
 *
 * Returning null rather than an object of empty arrays matters: an empty
 * context must not be stored on the target, so the UI can tell "no context was
 * given" apart from "context was given and nothing matched".
 */
export function buildPersonContext(input: PersonContextInput): PersonContext | null {
  const context: PersonContext = {
    known_usernames: list(input.knownUsernames),
    profile_urls: list(input.profileUrls),
    organizations: list(input.organizations),
    schools: list(input.schools),
    country: input.country.trim() || null,
    city: input.city.trim() || null,
  };
  const empty =
    context.known_usernames?.length === 0 &&
    context.profile_urls?.length === 0 &&
    context.organizations?.length === 0 &&
    context.schools?.length === 0 &&
    !context.country &&
    !context.city;
  return empty ? null : context;
}
