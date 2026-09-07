import { describe, expect, it } from "vitest";

import {
  EMPTY_PERSON_CONTEXT,
  SELECTABLE_TYPES,
  TYPE_HELP,
  ambiguityChoices,
  buildPersonContext,
  canSubmitTarget,
  requiresExplicitType,
} from "@/lib/targets";
import type { NormalizationPreview, TargetType } from "@/types/api";

function preview(overrides: Partial<NormalizationPreview> = {}): NormalizationPreview {
  return {
    raw_input: "Timotheous Samar",
    type: null,
    normalized_value: "",
    attributes: {},
    ambiguous: false,
    candidates: [],
    message: "",
    ...overrides,
  };
}

const ambiguous = preview({
  ambiguous: true,
  candidates: ["PERSON", "ORGANIZATION"],
  message: "'Timotheous Samar' could name either a person or an organisation.",
});

const resolved = preview({ type: "PERSON", normalized_value: "timotheous samar" });

describe("requiresExplicitType", () => {
  it("demands a choice for a name the backend would not classify", () => {
    expect(requiresExplicitType(ambiguous, "")).toBe(true);
  });

  it("is satisfied once a type is chosen", () => {
    expect(requiresExplicitType(ambiguous, "PERSON")).toBe(false);
    expect(requiresExplicitType(ambiguous, "ORGANIZATION")).toBe(false);
  });

  it("never demands a choice for structured input", () => {
    expect(requiresExplicitType(preview({ type: "DOMAIN" }), "")).toBe(false);
  });

  it("makes no demand before a preview has arrived", () => {
    expect(requiresExplicitType(null, "")).toBe(false);
  });
});

describe("canSubmitTarget", () => {
  it("blocks submission of an unclassified name", () => {
    expect(canSubmitTarget("Timotheous Samar", ambiguous, "", false)).toBe(false);
  });

  it("allows submission once the investigator has classified it", () => {
    expect(canSubmitTarget("Timotheous Samar", ambiguous, "PERSON", false)).toBe(true);
  });

  it("allows submission of structured input with no type chosen", () => {
    expect(canSubmitTarget("example.com", preview({ type: "DOMAIN" }), "", false)).toBe(true);
  });

  it("blocks an empty or whitespace-only value", () => {
    expect(canSubmitTarget("", resolved, "PERSON", false)).toBe(false);
    expect(canSubmitTarget("   ", resolved, "PERSON", false)).toBe(false);
  });

  it("blocks while a request is in flight", () => {
    expect(canSubmitTarget("example.com", preview({ type: "DOMAIN" }), "", true)).toBe(false);
  });
});

describe("ambiguityChoices", () => {
  it("offers exactly the candidates the backend named", () => {
    expect(ambiguityChoices(ambiguous)).toEqual(["PERSON", "ORGANIZATION"]);
  });

  it("offers nothing when the input is not ambiguous", () => {
    expect(ambiguityChoices(resolved)).toEqual([]);
    expect(ambiguityChoices(null)).toEqual([]);
  });

  it("falls back to person or organisation if a backend sends none", () => {
    const choices = ambiguityChoices(preview({ ambiguous: true, candidates: [] }));
    expect(choices).toEqual(["PERSON", "ORGANIZATION"]);
  });
});

describe("selectable types", () => {
  it("offers PERSON and ORGANIZATION as separate, explicit choices", () => {
    expect(SELECTABLE_TYPES).toContain("PERSON");
    expect(SELECTABLE_TYPES).toContain("ORGANIZATION");
  });

  it("lists the two ambiguous types first, where the choice is made", () => {
    expect(SELECTABLE_TYPES.slice(0, 2)).toEqual(["PERSON", "ORGANIZATION"]);
  });

  it("explains what each ambiguous choice means", () => {
    for (const type of ["PERSON", "ORGANIZATION"] as TargetType[]) {
      expect(TYPE_HELP[type]).toBeTruthy();
    }
  });

  it("tells the investigator that a person is searched by name only", () => {
    expect(TYPE_HELP.PERSON).toMatch(/no infrastructure lookups/i);
  });
});

describe("buildPersonContext", () => {
  it("returns null when nothing was supplied", () => {
    expect(buildPersonContext(EMPTY_PERSON_CONTEXT)).toBeNull();
  });

  it("returns null for whitespace, so an empty context is never stored", () => {
    expect(
      buildPersonContext({ ...EMPTY_PERSON_CONTEXT, organizations: "  ,  , " }),
    ).toBeNull();
  });

  it("splits comma-separated lists and trims each entry", () => {
    const context = buildPersonContext({
      ...EMPTY_PERSON_CONTEXT,
      knownUsernames: " octocat , example_user ",
    });
    expect(context?.known_usernames).toEqual(["octocat", "example_user"]);
  });

  it("keeps a single city or country as a scalar", () => {
    const context = buildPersonContext({ ...EMPTY_PERSON_CONTEXT, city: " Delft " });
    expect(context?.city).toBe("Delft");
    expect(context?.country).toBeNull();
  });

  it("carries every supported anchor through", () => {
    const context = buildPersonContext({
      knownUsernames: "octocat",
      profileUrls: "https://github.com/octocat",
      websites: "https://example.com",
      organizations: "Example Ltd",
      schools: "Example University",
      occupation: "researcher",
      orcid: "0000-0002-1825-0097",
      githubUsername: "octocat",
      country: "Netherlands",
      city: "Delft",
    });
    expect(context).toEqual({
      known_usernames: ["octocat"],
      profile_urls: ["https://github.com/octocat"],
      websites: ["https://example.com"],
      organizations: ["Example Ltd"],
      schools: ["Example University"],
      occupation: "researcher",
      orcid: "0000-0002-1825-0097",
      github_username: "octocat",
      country: "Netherlands",
      city: "Delft",
    });
  });

  it("accepts an exact identifier on its own", () => {
    const context = buildPersonContext({ ...EMPTY_PERSON_CONTEXT, orcid: "0000-0002-1825-0097" });
    expect(context?.orcid).toBe("0000-0002-1825-0097");
    expect(context?.known_usernames).toEqual([]);
  });
});
