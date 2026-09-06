import { describe, expect, it } from "vitest";

import {
  FREE_PERSON_COLLECTORS,
  isCandidate,
  isSubject,
  rankCandidates,
  summariseRuns,
  toCandidate,
} from "@/lib/candidates";
import type { CollectorRun, Entity } from "@/types/api";

function entity(overrides: Partial<Entity> & { canonical_value: string }): Entity {
  return {
    id: overrides.canonical_value,
    case_id: "case-1",
    type: "PERSONA",
    display_name: "Timotheous Samar",
    aliases: [],
    attributes: {},
    confidence: 0.2,
    confidence_reasons: [],
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    source_finding_ids: [],
    ...overrides,
  };
}

function candidateEntity(url: string, attributes: Record<string, unknown> = {}, confidence = 0.2) {
  return entity({
    canonical_value: `person-candidate:${url}`,
    confidence,
    attributes: {
      source: "orcid",
      source_label: "ORCID",
      reference_url: url,
      candidate_name: "Timotheous Samar",
      match_reasons: ["The source spells the name exactly as searched"],
      mismatch_reasons: ["Nothing beyond the name connects this record to the subject"],
      corroborated_by: [],
      affiliations: [],
      locations: [],
      identifiers: {},
      ...attributes,
    },
  });
}

function run(collector: string, overrides: Partial<CollectorRun> = {}): CollectorRun {
  return {
    id: collector,
    target_id: "t1",
    collector,
    collector_version: "1.0.0",
    source_attribution: null,
    status: "SUCCESS",
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_type: null,
    error_message: null,
    stats: {},
    ...overrides,
  };
}

describe("classifying persona entities", () => {
  it("tells the subject apart from the candidates", () => {
    const subject = entity({ canonical_value: "person:timotheous samar" });
    const candidate = candidateEntity("https://orcid.org/1");

    expect(isSubject(subject)).toBe(true);
    expect(isCandidate(subject)).toBe(false);
    expect(isCandidate(candidate)).toBe(true);
    expect(isSubject(candidate)).toBe(false);
  });

  it("ignores entities that are not people", () => {
    const domain = entity({ canonical_value: "example.com", type: "DOMAIN" });
    expect(isCandidate(domain)).toBe(false);
    expect(isSubject(domain)).toBe(false);
  });
});

describe("toCandidate", () => {
  it("reads the reasons the backend recorded, both for and against", () => {
    const candidate = toCandidate(candidateEntity("https://orcid.org/1"));
    expect(candidate.matchReasons).toHaveLength(1);
    expect(candidate.mismatchReasons).toHaveLength(1);
    expect(candidate.sourceLabel).toBe("ORCID");
  });

  it("survives an entity with no attributes at all", () => {
    const bare = entity({ canonical_value: "person-candidate:https://x/1", attributes: {} });
    const candidate = toCandidate(bare);
    expect(candidate.matchReasons).toEqual([]);
    expect(candidate.affiliations).toEqual([]);
    expect(candidate.identifiers).toEqual({});
  });
});

describe("rankCandidates", () => {
  it("keeps two same-name records as two candidates", () => {
    const ranked = rankCandidates([
      candidateEntity("https://orcid.org/1"),
      candidateEntity("https://orcid.org/2"),
    ]);
    expect(ranked).toHaveLength(2);
    expect(new Set(ranked.map((item) => item.url)).size).toBe(2);
  });

  it("puts corroborated candidates first, whatever their confidence", () => {
    const ranked = rankCandidates([
      candidateEntity("https://orcid.org/plain", {}, 0.3),
      candidateEntity("https://orcid.org/backed", { corroborated_by: ["affiliation"] }, 0.25),
    ]);
    expect(ranked[0]?.url).toBe("https://orcid.org/backed");
  });

  it("excludes the subject from the candidate list", () => {
    const ranked = rankCandidates([
      entity({ canonical_value: "person:timotheous samar" }),
      candidateEntity("https://orcid.org/1"),
    ]);
    expect(ranked).toHaveLength(1);
  });
});

describe("summariseRuns", () => {
  it("marks the key-free sources as free and the search collector as not", () => {
    const summary = summariseRuns([run("orcid"), run("search")], []);
    const byName = Object.fromEntries(summary.map((item) => [item.collector, item]));
    expect(byName["orcid"]?.free).toBe(true);
    expect(byName["search"]?.free).toBe(false);
  });

  it("counts the candidates each source produced", () => {
    const summary = summariseRuns(
      [run("orcid"), run("wikidata")],
      [
        { ...toCandidate(candidateEntity("https://orcid.org/1")), source: "orcid" },
        { ...toCandidate(candidateEntity("https://orcid.org/2")), source: "orcid" },
      ],
    );
    const byName = Object.fromEntries(summary.map((item) => [item.collector, item]));
    expect(byName["orcid"]?.candidates).toBe(2);
    expect(byName["wikidata"]?.candidates).toBe(0);
  });

  it("keeps a skipped source visible with its reason", () => {
    const summary = summariseRuns(
      [run("reddit", { status: "SKIPPED", error_message: "Reddit refused the request" })],
      [],
    );
    expect(summary[0]?.status).toBe("SKIPPED");
    expect(summary[0]?.reason).toBe("Reddit refused the request");
  });

  it("lists free sources before paid ones", () => {
    const summary = summariseRuns([run("search"), run("orcid")], []);
    expect(summary[0]?.collector).toBe("orcid");
  });
});

describe("the free collector list", () => {
  it("does not include the paid search collector", () => {
    expect(FREE_PERSON_COLLECTORS).not.toContain("search");
  });

  it("covers every free source the backend runs for a person", () => {
    expect([...FREE_PERSON_COLLECTORS].sort()).toEqual([
      "crossref",
      "github_people",
      "openalex",
      "orcid",
      "person_usernames",
      "reddit",
      "wikidata",
    ]);
  });
});
