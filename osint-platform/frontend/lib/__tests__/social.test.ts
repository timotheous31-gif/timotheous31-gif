import { describe, expect, it } from "vitest";

import {
  DECISIONS,
  awaitingReview,
  canShowThumbnail,
  correlationScore,
  decisionLabel,
  decisionTone,
  fetchStateLabel,
  hostOf,
  imagesBySource,
  canRenderThumbnail,
  discoveryLabel,
  discoveryTone,
  IMAGE_DISCLAIMER,
  leadingCaveat,
  nameComparison,
  orderedFacts,
  profilesByPlatform,
} from "@/lib/social";
import type {
  CandidateGroup,
  ImageEvidenceRecord,
  ProfileFact,
  SocialProfileRecord,
} from "@/types/api";

function profile(overrides: Partial<SocialProfileRecord> = {}): SocialProfileRecord {
  return {
    id: "p1", candidate_entity_id: null, platform: "linkedin", platform_label: "LinkedIn",
    handle: "example-person", profile_url: "https://www.linkedin.com/in/example-person",
    display_name: null, bio: null, source_url: null, accessibility: "RESTRICTED",
    server_fetchable: false, fetch_note: "LinkedIn refuses anonymous requests.",
    collector: "manual_search_recon", evidence_class: "investigator_imported",
    confidence: 0.15, match_reasons: [], mismatch_reasons: [], corroborated_by: [],
    retrieved_at: null, discovery_method: null, discovery_methods: [], discovered_from: null, discovered_from_all: [], profile_facts: [], declared_name: null, searched_name: null,
    name_relationship: null, detail_source_url: null, detail_note: null, decision: null,
    ...overrides,
  };
}

function image(overrides: Partial<ImageEvidenceRecord> = {}): ImageEvidenceRecord {
  return {
    id: "i1", candidate_entity_id: null, social_profile_id: null,
    image_url: "https://example.org/p.jpg", source_page_url: "https://example.org/faculty",
    platform: null, caption: null, context_text: null, fetch_state: "REFERENCE_ONLY",
    sha256: null, content_type: null, byte_length: null, width: null, height: null,
    redirects: [], final_url: null, fetch_note: null, origin: "manual_search_recon",
    evidence_class: "investigator_imported", retrieved_at: null, evidence_id: null,
    attributes: { analysis: "none", biometric_matching: false }, decision: null,
    ...overrides,
  };
}

describe("decisions", () => {
  it("offers exactly the four required judgements", () => {
    expect([...DECISIONS]).toEqual(["CONFIRMED", "REJECTED", "UNRESOLVED", "NEEDS_REVIEW"]);
  });

  it("says No decision rather than inventing one", () => {
    expect(decisionLabel(null)).toBe("No decision");
    expect(decisionTone(null)).toBe("SKIPPED");
  });

  it("distinguishes confirmed from rejected visually", () => {
    expect(decisionTone("CONFIRMED")).toBe("SUCCESS");
    expect(decisionTone("REJECTED")).toBe("FAILED");
    expect(decisionTone("CONFIRMED")).not.toBe(decisionTone("REJECTED"));
  });

  it("labels every decision", () => {
    for (const decision of DECISIONS) expect(decisionLabel(decision)).toBeTruthy();
  });
});

describe("confidence is presented separately from judgement", () => {
  it("renders the automated score from the record, untouched by a decision", () => {
    const rejected = profile({
      confidence: 0.72,
      decision: {
        id: "d", subject_type: "SOCIAL_PROFILE", subject_id: "p1",
        decision: "REJECTED", note: null, decided_by: null, decided_at: "2026-01-01T00:00:00Z",
      },
    });
    // The decision says no; the computed score is still what the platform found.
    expect(correlationScore(rejected.confidence)).toBe("0.72");
    expect(decisionLabel(rejected.decision!.decision)).toBe("Rejected");
  });

  it("shows a correlation score as a score, never as a percentage", () => {
    // `0.54` rendered as `54%` states a likelihood nobody computed. Two records
    // are never "72% the same person".
    expect(correlationScore(0)).toBe("0.00");
    expect(correlationScore(0.155)).toBe("0.15");
    expect(correlationScore(1)).toBe("1.00");
    expect(correlationScore(0.7)).not.toContain("%");
  });
});

describe("thumbnails", () => {
  it("shows one only for an image the platform actually fetched", () => {
    expect(canShowThumbnail(image({ fetch_state: "FETCHED", final_url: "https://example.org/p.jpg" }))).toBe(true);
  });

  it("never renders a URL the platform did not read", () => {
    expect(canShowThumbnail(image({ fetch_state: "REFERENCE_ONLY" }))).toBe(false);
    expect(canShowThumbnail(image({ fetch_state: "BLOCKED" }))).toBe(false);
  });

  it("explains what each fetch state means", () => {
    expect(fetchStateLabel("REFERENCE_ONLY")).toMatch(/not downloaded/i);
    expect(fetchStateLabel("FETCHED")).toMatch(/hashed/i);
    expect(fetchStateLabel("BLOCKED")).toMatch(/blocked/i);
  });
});

describe("grouping", () => {
  it("groups images by source, never by appearance", () => {
    const grouped = imagesBySource([
      image({ id: "a", platform: "linkedin" }),
      image({ id: "b", platform: "linkedin" }),
      image({ id: "c", source_page_url: "https://example.org/x" }),
    ]);
    expect(grouped.get("linkedin")?.map((item) => item.id)).toEqual(["a", "b"]);
    expect(grouped.get("example.org")?.map((item) => item.id)).toEqual(["c"]);
  });

  it("groups profiles by platform", () => {
    const grouped = profilesByPlatform([
      profile({ id: "a" }),
      profile({ id: "b", platform: "twitter", platform_label: "X (Twitter)" }),
    ]);
    expect([...grouped.keys()]).toEqual(["LinkedIn", "X (Twitter)"]);
  });

  it("reads a host without throwing on rubbish", () => {
    expect(hostOf("https://www.example.org/a")).toBe("example.org");
    expect(hostOf("not a url")).toBe("");
  });
});

describe("awaitingReview", () => {
  function group(overrides: Partial<CandidateGroup> = {}): CandidateGroup {
    return {
      entity_id: "e1", display_name: "Candidate A", canonical_value: "c",
      confidence: 0.2, confidence_reasons: [], match_reasons: [], mismatch_reasons: [],
      corroborated_by: [], identity_established: false,
      social_profiles: [], images: [], public_contacts: [], decision: null,
      ...overrides,
    };
  }

  it("collects everything with no decision yet", () => {
    const result = awaitingReview([group({ social_profiles: [profile()], images: [image()] })]);
    expect(result.profiles).toHaveLength(1);
    expect(result.images).toHaveLength(1);
  });

  it("keeps NEEDS_REVIEW in the queue and drops settled items", () => {
    const decided = (value: "CONFIRMED" | "NEEDS_REVIEW") => ({
      id: "d", subject_type: "SOCIAL_PROFILE" as const, subject_id: "p1",
      decision: value, note: null, decided_by: null, decided_at: "2026-01-01T00:00:00Z",
    });
    const result = awaitingReview([
      group({
        social_profiles: [
          profile({ id: "a", decision: decided("CONFIRMED") }),
          profile({ id: "b", decision: decided("NEEDS_REVIEW") }),
        ],
      }),
    ]);
    expect(result.profiles.map((item) => item.id)).toEqual(["b"]);
  });
});

describe("leadingCaveat", () => {
  it("surfaces the first reason against, so a score is never read alone", () => {
    expect(leadingCaveat(profile({ mismatch_reasons: ["Nothing beyond the name"] }))).toBe(
      "Nothing beyond the name",
    );
  });

  it("returns null when there is nothing against it", () => {
    expect(leadingCaveat(profile())).toBeNull();
  });
});

describe("profile self-description", () => {
  const fact = (overrides: Partial<ProfileFact> = {}): ProfileFact => ({
    kind: "occupation",
    label: "Occupation / title",
    value: "Lecturer in English (BPS-17)",
    source_line: "Lecturer in English (BPS-17), Government of Sindh, Pakistan",
    basis: "phrase",
    matched_term: "lecturer",
    source_url: "https://github.com/example-person/example-person/blob/main/README.md",
    ...overrides,
  });

  it("shows the declared name beside the searched one, never instead of it", () => {
    const result = nameComparison(
      profile({
        searched_name: "Example Person",
        declared_name: "Example Person Dass",
        name_relationship: {
          relationship: "extends_searched_name",
          explanation: "The name under investigation is unchanged.",
        },
      }),
    );
    expect(result).toEqual({
      searched: "Example Person",
      declared: "Example Person Dass",
      explanation: "The name under investigation is unchanged.",
    });
  });

  it("says nothing when the source declares no name", () => {
    expect(nameComparison(profile())).toBeNull();
  });

  it("orders statements the way an analyst reads them", () => {
    const facts = [
      fact({ kind: "location", value: "Pakistan" }),
      fact({ kind: "employer", value: "Government of Sindh" }),
      fact({ kind: "occupation" }),
    ];
    expect(orderedFacts(profile({ profile_facts: facts })).map((item) => item.kind)).toEqual([
      "occupation",
      "employer",
      "location",
    ]);
  });

  it("carries a geographic statement's limit through to the reader", () => {
    const geographic = fact({
      kind: "location",
      label: "Public geographic association",
      value: "Pakistan",
      interpretation: "It is not a claim of nationality, citizenship, residence or location.",
    });
    const only = orderedFacts(profile({ profile_facts: [geographic] }))[0]!;
    expect(only.label).toBe("Public geographic association");
    expect(only.interpretation).toContain("not a claim of nationality");
  });

  it("keeps the line a statement was read from, so it can be checked", () => {
    const only = orderedFacts(profile({ profile_facts: [fact()] }))[0]!;
    expect(only.source_line).toContain("Government of Sindh");
    expect(only.value).toBe("Lecturer in English (BPS-17)");
  });
});

describe("discovery method", () => {
  it("distinguishes what you supplied from what a search returned", () => {
    expect(discoveryLabel("supplied_anchor")).toContain("You supplied");
    expect(discoveryLabel("name_search")).toContain("public search");
    expect(discoveryLabel("published_link")).toContain("Linked from");
    expect(discoveryTone("supplied_anchor")).toBe("SUCCESS");
    expect(discoveryTone("name_search")).toBe("SKIPPED");
  });

  it("says the method is unrecorded rather than inventing one", () => {
    expect(discoveryLabel(null)).toBe("Discovery method not recorded");
    expect(discoveryTone(null)).toBeUndefined();
  });
});

describe("thumbnail safety", () => {
  const withUrl = (image_url: string) => image({ image_url });

  it("draws an https public image", () => {
    expect(canRenderThumbnail(withUrl("https://example.com/portrait.jpg"))).toBe(true);
  });

  it.each([
    "http://example.com/portrait.jpg",
    "https://127.0.0.1/portrait.jpg",
    "https://10.1.2.3/portrait.jpg",
    "https://192.168.0.9/portrait.jpg",
    "https://172.16.0.1/portrait.jpg",
    "https://169.254.169.254/portrait.jpg",
    "https://localhost/portrait.jpg",
    "not a url",
    "",
  ])("refuses %s", (url) => {
    expect(canRenderThumbnail(withUrl(url))).toBe(false);
  });

  it("states the limit that travels with every picture", () => {
    expect(IMAGE_DISCLAIMER).toContain("does not independently establish identity");
  });
});
