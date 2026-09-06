import { describe, expect, it } from "vitest";

import {
  SELECTABLE_TYPES,
  TYPE_HELP,
  ambiguityChoices,
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
