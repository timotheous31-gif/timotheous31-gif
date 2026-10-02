import { describe, expect, it } from "vitest";

import {
  candidatePosture,
  coverage,
  graphSnapshot,
  initialsOf,
  latestJob,
  portraitFor,
  primarySubject,
  recordedDecisions,
  reviewState,
  statusCells,
  suppliedContext,
  tabCounts,
  targetTypeLabel,
  timelinePreview,
} from "@/lib/case-workspace";
import type {
  CandidateGroup,
  Case,
  CaseSummary,
  CollectorRun,
  Entity,
  GraphResponse,
  ImageEvidenceRecord,
  Job,
  Target,
  TimelineEvent,
} from "@/types/api";

const CASE: Case = {
  id: "c1",
  name: "A matter",
  description: null,
  status: "RUNNING",
  notes: null,
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

function target(overrides: Partial<Target> & Pick<Target, "id" | "type" | "raw_input">): Target {
  return {
    case_id: "c1",
    normalized_value: overrides.raw_input.toLowerCase(),
    status: "PENDING",
    notes: null,
    attributes: {},
    tags: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as Target;
}

function group(overrides: Partial<CandidateGroup>): CandidateGroup {
  return {
    entity_id: "e1",
    display_name: "Someone",
    canonical_value: "person-candidate:x",
    confidence: 0.5,
    confidence_reasons: [],
    match_reasons: [],
    mismatch_reasons: [],
    corroborated_by: [],
    presentation: "PRIMARY",
    presentation_reason: "",
    identity_established: false,
    social_profiles: [],
    images: [],
    public_contacts: [],
    decision: null,
    ...overrides,
  } as CandidateGroup;
}

function run(status: CollectorRun["status"], collector = "orcid"): CollectorRun {
  return {
    id: `${collector}-${status}`,
    target_id: "t1",
    collector,
    collector_version: "1.0.0",
    source_attribution: null,
    status,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_type: null,
    error_message: null,
    stats: {},
  };
}

function job(overrides: Partial<Job> & Pick<Job, "id" | "state">): Job {
  return {
    case_id: "c1",
    celery_id: null,
    progress: 0,
    message: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    params: {},
    result: {},
    cancel_requested: false,
    created_at: "2026-01-01T00:00:00Z",
    effective_state: "",
    processing_available: true,
    ...overrides,
  } as Job;
}

describe("primarySubject", () => {
  it("leads with the PERSON even when another target was added first", () => {
    const { target: lead, others } = primarySubject([
      target({ id: "d", type: "DOMAIN", raw_input: "example.com", created_at: "2026-01-01T00:00:00Z" }),
      target({ id: "p", type: "PERSON", raw_input: "Sara Samara", created_at: "2026-02-01T00:00:00Z" }),
    ]);
    expect(lead?.id).toBe("p");
    expect(others).toBe(1);
  });

  it("falls back to the oldest target when there is no person", () => {
    const { target: lead, others } = primarySubject([
      target({ id: "b", type: "DOMAIN", raw_input: "b.example", created_at: "2026-03-01T00:00:00Z" }),
      target({ id: "a", type: "DOMAIN", raw_input: "a.example", created_at: "2026-01-01T00:00:00Z" }),
    ]);
    expect(lead?.id).toBe("a");
    expect(others).toBe(1);
  });

  it("reports nothing rather than guessing when the case has no targets", () => {
    expect(primarySubject([])).toEqual({ target: null, others: 0 });
  });
});

describe("portraitFor", () => {
  const person = target({ id: "p", type: "PERSON", raw_input: "Sara Samara" });

  const entity = (id: string, confidence: number): Entity =>
    ({
      id,
      case_id: "c1",
      type: "PERSONA",
      display_name: "Sara Samara",
      canonical_value: `person-candidate:${id}`,
      aliases: [],
      attributes: { candidate_name: "Sara Samara" },
      confidence,
      confidence_reasons: [],
      notes: null,
      created_at: "2026-01-01T00:00:00Z",
      source_finding_ids: [],
    }) as Entity;

  const image = (overrides: Partial<ImageEvidenceRecord>): ImageEvidenceRecord =>
    ({
      id: "i",
      candidate_entity_id: "e1",
      social_profile_id: null,
      image_url: "https://example.test/a.png",
      source_page_url: "https://example.test/profile",
      platform: "github",
      caption: null,
      context_text: null,
      fetch_state: "FETCHED",
      sha256: "abc",
      content_type: "image/png",
      byte_length: 10,
      width: 10,
      height: 10,
      redirects: [],
      final_url: null,
      fetch_note: null,
      origin: "PROFILE_AVATAR",
      evidence_class: "SUPPLIED_HANDLE",
      retrieved_at: "2026-01-01T00:00:00Z",
      evidence_id: null,
      attributes: {},
      decision: null,
      ...overrides,
    }) as ImageEvidenceRecord;

  it("shows nothing at all for a case with no PERSON target", () => {
    expect(
      portraitFor({
        targets: [target({ id: "d", type: "DOMAIN", raw_input: "example.com" })],
        images: [image({})],
        entities: [entity("e1", 0.9)],
      }),
    ).toBeNull();
  });

  it("refuses an image the platform never fetched", () => {
    // Rendering it would make the investigator's browser request it from a page
    // nobody vetted, and it would look like verified evidence.
    expect(
      portraitFor({
        targets: [person],
        images: [image({ fetch_state: "REFERENCE_ONLY" })],
        entities: [entity("e1", 0.9)],
      }),
    ).toBeNull();
  });

  it("refuses an image that is not filed against any candidate", () => {
    expect(
      portraitFor({
        targets: [person],
        images: [image({ candidate_entity_id: null })],
        entities: [entity("e1", 0.9)],
      }),
    ).toBeNull();
  });

  it("prefers the analyst-confirmed candidate over the higher-scoring one", () => {
    const portrait = portraitFor({
      targets: [person],
      images: [
        image({ id: "high", candidate_entity_id: "e2", image_url: "https://example.test/high.png" }),
        image({
          id: "confirmed",
          candidate_entity_id: "e1",
          image_url: "https://example.test/confirmed.png",
          decision: {
            id: "d",
            subject_type: "CANDIDATE",
            subject_id: "e1",
            decision: "CONFIRMED",
            note: null,
            decided_by: "a",
            decided_at: "2026-01-01T00:00:00Z",
          },
        }),
      ],
      entities: [entity("e1", 0.3), entity("e2", 0.95)],
    });
    expect(portrait?.imageUrl).toBe("https://example.test/confirmed.png");
    expect(portrait?.confirmed).toBe(true);
  });

  it("falls back to the strongest-scoring candidate when nothing is confirmed", () => {
    const portrait = portraitFor({
      targets: [person],
      images: [
        image({ id: "low", candidate_entity_id: "e1", image_url: "https://example.test/low.png" }),
        image({ id: "high", candidate_entity_id: "e2", image_url: "https://example.test/high.png" }),
      ],
      entities: [entity("e1", 0.3), entity("e2", 0.95)],
    });
    expect(portrait?.imageUrl).toBe("https://example.test/high.png");
    expect(portrait?.confirmed).toBe(false);
  });

  it("prefers the URL the platform actually resolved to", () => {
    const portrait = portraitFor({
      targets: [person],
      images: [image({ final_url: "https://cdn.example.test/final.png" })],
      entities: [entity("e1", 0.9)],
    });
    expect(portrait?.imageUrl).toBe("https://cdn.example.test/final.png");
  });
});

describe("initialsOf", () => {
  it("takes at most two initials", () => {
    expect(initialsOf("Sara Samara")).toBe("SS");
    expect(initialsOf("a.mitchell@example.com")).toBe("AM");
    expect(initialsOf("")).toBe("?");
  });
});

describe("coverage", () => {
  it("counts a timeout as a failure and keeps skips separate", () => {
    const cover = coverage([
      run("SUCCESS", "orcid"),
      run("SUCCESS", "openalex"),
      run("PARTIAL", "github"),
      run("SKIPPED", "reddit"),
      run("FAILED", "crossref"),
      run("TIMEOUT", "wikidata"),
    ]);
    expect(cover).toEqual({ ran: 6, succeeded: 2, failed: 2, skipped: 1, partial: 1 });
  });
});

describe("candidatePosture", () => {
  it("separates what is shown from what an analyst decided", () => {
    const decision = (value: "CONFIRMED" | "REJECTED") => ({
      id: `d-${value}`,
      subject_type: "CANDIDATE" as const,
      subject_id: "e",
      decision: value,
      note: null,
      decided_by: "a",
      decided_at: "2026-01-01T00:00:00Z",
    });
    const posture = candidatePosture([
      group({ presentation: "PRIMARY", decision: decision("CONFIRMED") }),
      group({ presentation: "PRIMARY" }),
      group({ presentation: "LOW_CONFIDENCE" }),
      group({ presentation: "LOW_CONFIDENCE" }),
      group({ presentation: "REJECTED", decision: decision("REJECTED") }),
    ]);
    expect(posture).toEqual({
      total: 5,
      primary: 2,
      lowConfidence: 2,
      confirmed: 1,
      rejected: 1,
      unresolved: 3,
    });
  });
});

describe("reviewState", () => {
  it("tells apart a candidate nobody looked at from one nobody could settle", () => {
    expect(reviewState(null).state).toBe("AUTOMATED_ONLY");
    expect(reviewState("NEEDS_REVIEW").state).toBe("AWAITING_REVIEW");
    expect(reviewState("UNRESOLVED").state).toBe("AWAITING_REVIEW");
    expect(reviewState("CONFIRMED").state).toBe("CONFIRMED");
    expect(reviewState("REJECTED").state).toBe("RULED_OUT");
  });

  it("gives every state a glyph, so none of them needs colour", () => {
    for (const value of [null, "NEEDS_REVIEW", "CONFIRMED", "REJECTED"] as const) {
      expect(reviewState(value).glyph).not.toBe("");
    }
  });
});

describe("recordedDecisions", () => {
  it("returns only recorded decisions, newest first", () => {
    const rows = recordedDecisions([
      group({ display_name: "No decision" }),
      group({
        display_name: "Old",
        decision: {
          id: "d1", subject_type: "CANDIDATE", subject_id: "e", decision: "CONFIRMED",
          note: null, decided_by: "a", decided_at: "2026-01-01T00:00:00Z",
        },
      }),
      group({
        display_name: "New",
        decision: {
          id: "d2", subject_type: "CANDIDATE", subject_id: "e", decision: "REJECTED",
          note: "no", decided_by: "a", decided_at: "2026-05-01T00:00:00Z",
        },
      }),
    ]);
    expect(rows.map((row) => row.subject)).toEqual(["New", "Old"]);
  });
});

describe("latestJob", () => {
  it("picks the most recently created job", () => {
    expect(
      latestJob([
        job({ id: "old", state: "COMPLETE", created_at: "2026-01-01T00:00:00Z" }),
        job({ id: "new", state: "RUNNING", created_at: "2026-02-01T00:00:00Z" }),
      ])?.id,
    ).toBe("new");
    expect(latestJob([])).toBeNull();
  });
});

describe("statusCells", () => {
  const summary = { findings: 7, evidence: 4 } as CaseSummary;

  const cells = (overrides: Partial<Parameters<typeof statusCells>[0]> = {}) =>
    statusCells({
      caseRecord: CASE,
      summary,
      runs: [],
      jobs: [],
      groups: [],
      caseId: "c1",
      ...overrides,
    });

  it("gives six cells, each with a word as well as a tone", () => {
    const strip = cells();
    expect(strip).toHaveLength(6);
    for (const cell of strip) {
      expect(cell.value).not.toBe("");
      expect(cell.detail).not.toBe("");
    }
  });

  it("raises an alert when a queue has no worker, not merely when a run failed", () => {
    const strip = cells({
      jobs: [
        job({
          id: "j",
          state: "QUEUED",
          effective_state: "PROCESSING_UNAVAILABLE",
          processing_available: false,
        }),
      ],
    });
    expect(strip.find((cell) => cell.key === "run")?.tone).toBe("ALERT");
  });

  it("says how many runs succeeded out of how many ran", () => {
    const strip = cells({ runs: [run("SUCCESS", "a"), run("SUCCESS", "b"), run("FAILED", "c")] });
    const cover = strip.find((cell) => cell.key === "coverage")!;
    expect(cover.value).toBe("2/3");
    expect(cover.detail).toContain("2 of 3 runs succeeded");
    expect(cover.tone).toBe("ALERT");
  });

  it("stays neutral on coverage when nothing has run, rather than reporting failure", () => {
    expect(cells().find((cell) => cell.key === "coverage")?.tone).toBe("NEUTRAL");
  });

  it("reports what a report would contain, and never grades it", () => {
    const report = cells().find((cell) => cell.key === "report")!;
    expect(report.value).toBe("7 findings");
    expect(report.tone).toBe("NEUTRAL");
    expect(`${report.value} ${report.detail}`.toLowerCase()).not.toMatch(
      /ready|readiness|score|%|grade/,
    );
  });

  it("says a report has nothing to describe when no finding exists", () => {
    const report = cells({ summary: { findings: 0, evidence: 0 } as CaseSummary }).find(
      (cell) => cell.key === "report",
    )!;
    expect(report.value).toBe("Empty");
  });

  it("never invents a risk, threat or priority", () => {
    const text = cells({ runs: [run("FAILED")], jobs: [job({ id: "j", state: "FAILED" })] })
      .flatMap((cell) => [cell.label, cell.value, cell.detail])
      .join(" ")
      .toLowerCase();
    expect(text).not.toMatch(/risk|threat|priority|severity/);
  });
});

describe("graphSnapshot", () => {
  const graph = (edges: number): GraphResponse =>
    ({
      nodes: [],
      edges: [],
      stats: { node_count: 3, edge_count: edges, entity_types: [], relationship_types: [] },
      summary: {
        node_count: 3,
        edge_count: edges,
        entity_type_counts: { PERSONA: 2, DOMAIN: 1 },
        relationship_type_counts: {},
        component_count: 1,
        largest_component_size: 3,
        most_connected: [{ id: "a", label: "A", type: "PERSONA", degree: 2 }],
      },
    }) as GraphResponse;

  it("returns nothing when there are no relationships, so no network is drawn", () => {
    expect(graphSnapshot(graph(0))).toBeNull();
    expect(graphSnapshot(null)).toBeNull();
  });

  it("orders entity types by how many there are", () => {
    expect(graphSnapshot(graph(2))?.types).toEqual([
      ["PERSONA", 2],
      ["DOMAIN", 1],
    ]);
  });
});

describe("timelinePreview", () => {
  it("returns the newest events first, capped", () => {
    const event = (id: string, at: string) =>
      ({ id, occurred_at: at, title: id }) as TimelineEvent;
    const rows = timelinePreview(
      [event("old", "2020-01-01T00:00:00Z"), event("new", "2026-01-01T00:00:00Z"), event("mid", "2023-01-01T00:00:00Z")],
      2,
    );
    expect(rows.map((row) => row.id)).toEqual(["new", "mid"]);
  });
});

describe("tabCounts", () => {
  it("leaves a count undefined rather than drawing an unloaded section as empty", () => {
    const counts = tabCounts(null, null);
    expect(counts.findings).toBeUndefined();
    expect(counts.candidates).toBeUndefined();
  });

  it("shows a real zero", () => {
    const counts = tabCounts({ findings: 0, targets: 2 } as CaseSummary, []);
    expect(counts.findings).toBe(0);
    expect(counts.candidates).toBe(0);
  });
});

describe("suppliedContext", () => {
  it("reads the investigator's context and drops the empty keys", () => {
    const entries = suppliedContext(
      target({
        id: "p",
        type: "PERSON",
        raw_input: "Sara",
        attributes: { context: { affiliations: ["Example"], locations: [], known_usernames: ["s"] } },
      }),
    );
    expect(entries).toEqual([
      { label: "Affiliations", values: ["Example"] },
      { label: "Known usernames", values: ["s"] },
    ]);
  });

  it("returns nothing for a target with no context at all", () => {
    expect(suppliedContext(target({ id: "d", type: "DOMAIN", raw_input: "x" }))).toEqual([]);
    expect(suppliedContext(null)).toEqual([]);
  });
});

describe("targetTypeLabel", () => {
  it("spells out the stored enum", () => {
    expect(targetTypeLabel("PERSON")).toBe("Person");
    expect(targetTypeLabel("SOCIAL_PROFILE")).toBe("Social profile");
  });
});
