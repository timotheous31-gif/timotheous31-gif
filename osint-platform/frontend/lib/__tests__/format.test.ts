import { describe, expect, it } from "vitest";

import {
  bandLabel,
  confidenceBand,
  formatBytes,
  formatConfidence,
  formatDate,
  formatDateTime,
  humanise,
  truncate,
} from "@/lib/format";

describe("confidenceBand", () => {
  // These thresholds mirror the backend's documented bands; if they drift, the
  // dashboard would label a finding differently from its own report.
  it.each([
    [1.0, "LIKELY_MATCH"],
    [0.9, "LIKELY_MATCH"],
    [0.89, "PROBABLE_MATCH"],
    [0.7, "PROBABLE_MATCH"],
    [0.69, "POSSIBLE_MATCH"],
    [0.5, "POSSIBLE_MATCH"],
    [0.49, "WEAK_ASSOCIATION"],
    [0, "WEAK_ASSOCIATION"],
  ])("maps %s to %s", (score, expected) => {
    expect(confidenceBand(score)).toBe(expected);
  });

  it("renders a readable label", () => {
    expect(bandLabel("LIKELY_MATCH")).toBe("Strong correlation");
    expect(bandLabel("PROBABLE_MATCH")).toBe("Moderate correlation");
    expect(bandLabel("POSSIBLE_MATCH")).toBe("Weak correlation");
    expect(bandLabel("WEAK_ASSOCIATION")).toBe("Name-level only");
    // No band may read as a probability claim.
    for (const band of ["LIKELY_MATCH", "PROBABLE_MATCH", "POSSIBLE_MATCH", "WEAK_ASSOCIATION"] as const) {
      expect(bandLabel(band).toLowerCase()).not.toContain("probable");
      expect(bandLabel(band).toLowerCase()).not.toContain("likely");
    }
  });
});

describe("formatting helpers", () => {
  it("formats dates without a timezone surprise", () => {
    expect(formatDate("2024-05-01T12:34:56Z")).toBe("2024-05-01");
    expect(formatDateTime("2024-05-01T12:34:56Z")).toBe("2024-05-01 12:34 UTC");
  });

  it("renders an em dash for missing dates", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
  });

  it("passes through unparseable dates rather than showing NaN", () => {
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  it("formats confidence to two decimals", () => {
    expect(formatConfidence(0.9)).toBe("0.90");
    expect(formatConfidence(0.456)).toBe("0.46");
  });

  it("humanises enum values", () => {
    expect(humanise("SOCIAL_ACCOUNT")).toBe("Social account");
    expect(humanise("DNS_RECORD")).toBe("Dns record");
  });

  it("truncates long values", () => {
    expect(truncate("abcdef", 3)).toBe("abc…");
    expect(truncate("abc", 10)).toBe("abc");
  });

  it("formats byte sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
