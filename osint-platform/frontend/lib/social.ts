/**
 * Reading social profiles, image evidence and analyst decisions for display.
 *
 * The distinction this module exists to keep visible: **automated confidence**
 * is what the platform computed, and an **analyst decision** is what a human
 * concluded. They are different claims with different authority. The UI shows
 * both, side by side, and never blends one into the other — so nothing here
 * derives a score from a decision or vice versa.
 */

import type {
  AnalystDecisionValue,
  ContactClassification,
  CandidateGroup,
  ImageEvidenceRecord,
  ImageFetchState,
  PublicContactRecord,
  ProfileFact,
  SocialProfileRecord,
} from "@/types/api";

/** The four judgements an analyst can record. */
export const DECISIONS: readonly AnalystDecisionValue[] = [
  "CONFIRMED",
  "REJECTED",
  "UNRESOLVED",
  "NEEDS_REVIEW",
];

/** Human label for a decision. */
export function decisionLabel(decision: AnalystDecisionValue | null | undefined): string {
  if (!decision) return "No decision";
  const labels: Record<AnalystDecisionValue, string> = {
    CONFIRMED: "Confirmed",
    REJECTED: "Rejected",
    UNRESOLVED: "Unresolved",
    NEEDS_REVIEW: "Needs review",
  };
  return labels[decision] ?? decision;
}

/** Badge tone for a decision, reusing the vocabulary the rest of the UI uses. */
export function decisionTone(decision: AnalystDecisionValue | null | undefined): string {
  if (!decision) return "SKIPPED";
  const tones: Record<AnalystDecisionValue, string> = {
    CONFIRMED: "SUCCESS",
    REJECTED: "FAILED",
    UNRESOLVED: "SKIPPED",
    NEEDS_REVIEW: "PARTIAL",
  };
  return tones[decision] ?? "SKIPPED";
}

/** Automated confidence as a percentage, for display only. */
export function confidencePercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

/** What a fetch state means, in words an investigator can act on. */
export function fetchStateLabel(state: ImageFetchState | string): string {
  const labels: Record<string, string> = {
    FETCHED: "Fetched and hashed",
    REFERENCE_ONLY: "Reference only — not downloaded",
    BLOCKED: "Blocked by the safety guard",
  };
  return labels[state] ?? state;
}

/**
 * Whether a thumbnail can be shown.
 *
 * Only for an image the platform actually fetched. Rendering a URL the platform
 * never read would make the browser fetch it on the investigator's behalf, from
 * a page they have not vetted — and it would look like evidence the platform
 * had verified when it is not.
 */
export function canShowThumbnail(image: ImageEvidenceRecord): boolean {
  return image.fetch_state === "FETCHED" && Boolean(image.final_url || image.image_url);
}

/** Group a candidate's images by the platform or domain they came from. */
export function imagesBySource(
  images: ImageEvidenceRecord[],
): Map<string, ImageEvidenceRecord[]> {
  const grouped = new Map<string, ImageEvidenceRecord[]>();
  for (const image of images) {
    const key = image.platform || hostOf(image.source_page_url) || "other";
    grouped.set(key, [...(grouped.get(key) ?? []), image]);
  }
  return grouped;
}

/** Group profiles by platform, so one candidate reads as one identity summary. */
export function profilesByPlatform(
  profiles: SocialProfileRecord[],
): Map<string, SocialProfileRecord[]> {
  const grouped = new Map<string, SocialProfileRecord[]>();
  for (const profile of profiles) {
    grouped.set(profile.platform_label, [
      ...(grouped.get(profile.platform_label) ?? []),
      profile,
    ]);
  }
  return grouped;
}

/** The host of a URL, or an empty string when it cannot be parsed. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** Everything still awaiting an analyst's judgement, across a case. */
export function awaitingReview(groups: CandidateGroup[]): {
  profiles: SocialProfileRecord[];
  images: ImageEvidenceRecord[];
} {
  const profiles: SocialProfileRecord[] = [];
  const images: ImageEvidenceRecord[] = [];
  for (const group of groups) {
    for (const profile of group.social_profiles) {
      if (!profile.decision || profile.decision.decision === "NEEDS_REVIEW") profiles.push(profile);
    }
    for (const image of group.images) {
      if (!image.decision || image.decision.decision === "NEEDS_REVIEW") images.push(image);
    }
  }
  return { profiles, images };
}

/**
 * A one-line reason a profile may not be the subject, or null when none apply.
 *
 * Surfaced next to the confidence so a high-looking number is never read
 * without the caveat that produced it.
 */
export function leadingCaveat(profile: SocialProfileRecord): string | null {
  return profile.mismatch_reasons[0] ?? null;
}


/**
 * How well established a contact is, in words.
 *
 * About provenance, never about how official the value looks — the label has to
 * carry that, because an address on a personal profile can look every bit as
 * authoritative as one on a company's contact page.
 */
export function classificationLabel(classification: ContactClassification | string): string {
  const labels: Record<string, string> = {
    VERIFIED_PUBLIC_BUSINESS: "Verified public business",
    PUBLIC_PROFESSIONAL: "Public professional registry",
    PUBLIC_SELF_PUBLISHED: "Self-published by the subject",
    UNVERIFIED_PUBLIC_REFERENCE: "Unverified public reference",
  };
  return labels[classification] ?? classification;
}

/** Badge tone: better-established provenance reads stronger. */
export function classificationTone(
  classification: ContactClassification | string,
): string | undefined {
  const tones: Record<string, string> = {
    VERIFIED_PUBLIC_BUSINESS: "SUCCESS",
    PUBLIC_PROFESSIONAL: "SUCCESS",
    PUBLIC_SELF_PUBLISHED: "PARTIAL",
    UNVERIFIED_PUBLIC_REFERENCE: "SKIPPED",
  };
  return tones[classification];
}

/** What to say when a case has no public contacts at all. */
export const NO_CONTACTS = "No verified public contact found.";

/** Contacts grouped by kind, so a list reads as email / phone / website. */
export function contactsByType(
  contacts: PublicContactRecord[],
): Map<string, PublicContactRecord[]> {
  const grouped = new Map<string, PublicContactRecord[]>();
  for (const contact of contacts) {
    grouped.set(contact.contact_type, [...(grouped.get(contact.contact_type) ?? []), contact]);
  }
  return grouped;
}

/** A mailto/tel/http link for a contact, or null when it is not linkable. */
export function contactHref(contact: PublicContactRecord): string | null {
  if (contact.contact_type === "EMAIL") return `mailto:${contact.value}`;
  if (contact.contact_type === "PHONE") return `tel:${contact.value.replace(/\s+/g, "")}`;
  if (contact.value.startsWith("http://") || contact.value.startsWith("https://")) {
    return contact.value;
  }
  return null;
}

/**
 * The declared name beside the searched one, when a source declares one.
 *
 * Returned as a pair rather than a single "best" name on purpose. The name
 * under investigation is what the investigator supplied; a source's spelling is
 * that source's claim, and showing them together is how an analyst sees the
 * difference instead of the platform quietly picking a winner.
 */
export function nameComparison(
  profile: SocialProfileRecord,
): { searched: string; declared: string; explanation: string } | null {
  if (!profile.declared_name || !profile.name_relationship) return null;
  return {
    searched: profile.searched_name ?? "—",
    declared: profile.declared_name,
    explanation: profile.name_relationship.explanation,
  };
}

/** Profile statements grouped by kind, in the order an analyst reads them. */
export const FACT_ORDER = [
  "occupation",
  "employer",
  "professional_field",
  "location",
  "declared_name",
] as const;

export function orderedFacts(profile: SocialProfileRecord): ProfileFact[] {
  const rank = (fact: ProfileFact) => {
    const index = (FACT_ORDER as readonly string[]).indexOf(fact.kind);
    return index === -1 ? FACT_ORDER.length : index;
  };
  return [...profile.profile_facts].sort((a, b) => rank(a) - rank(b));
}

/** What to say when a profile publishes no self-description we could read. */
export const NO_PROFILE_DETAIL = "This profile publishes no self-description we could read.";
