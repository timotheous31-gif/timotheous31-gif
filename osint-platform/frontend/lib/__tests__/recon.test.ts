import { describe, expect, it } from "vitest";

import {
  SEARCH_ENGINES,
  evidenceClassLabel,
  familyLabel,
  groupQueries,
  imageEvidence,
  searchUrl,
  socialResults,
  toImportPayload,
} from "@/lib/recon";
import type { ImportedResult, ReconQuery } from "@/types/api";

function query(overrides: Partial<ReconQuery> = {}): ReconQuery {
  return {
    query: '"Timotheous Samar"',
    family: "general",
    rationale: "The plainest search.",
    priority: 10,
    anchors_used: [],
    ...overrides,
  };
}

function result(overrides: Partial<ImportedResult> = {}): ImportedResult {
  return {
    id: "1",
    url: "https://www.linkedin.com/in/example",
    title: "Example — LinkedIn",
    snippet: "",
    query: '"Timotheous Samar" site:linkedin.com',
    engine: "Google",
    platform: "linkedin",
    platform_label: "LinkedIn",
    url_kind: "social",
    handle: "example",
    is_image: false,
    image_url: null,
    thumbnail_url: null,
    caption: null,
    evidence_class: "investigator_imported",
    imported_at: null,
    confidence: 0.2,
    evidence_sha256: ["abc123"],
    ...overrides,
  };
}

describe("searchUrl", () => {
  it("builds a link the investigator opens themselves", () => {
    const url = searchUrl('"Timotheous Samar" site:linkedin.com');
    expect(url).toContain("https://www.google.com/search?q=");
    expect(url).toContain(encodeURIComponent('"Timotheous Samar" site:linkedin.com'));
  });

  it("encodes operators so the query survives the round trip", () => {
    const url = searchUrl('"A B" site:x.com filetype:pdf');
    expect(url).not.toContain(" ");
    expect(url).toContain("%22A%20B%22");
  });

  it("supports each offered engine", () => {
    for (const engine of SEARCH_ENGINES) {
      expect(searchUrl("test", engine.key)).toContain(engine.url);
    }
  });

  it("falls back to the first engine for an unknown one", () => {
    expect(searchUrl("test", "Nope" as never)).toContain(SEARCH_ENGINES[0]!.url);
  });
});

describe("groupQueries", () => {
  it("groups by family and preserves the backend's order", () => {
    const grouped = groupQueries([
      query({ query: "a", family: "anchor" }),
      query({ query: "b", family: "social" }),
      query({ query: "c", family: "anchor" }),
    ]);
    expect(grouped.get("anchor")?.map((item) => item.query)).toEqual(["a", "c"]);
    expect(grouped.get("social")?.map((item) => item.query)).toEqual(["b"]);
  });

  it("labels the anchored family as the one to run first", () => {
    expect(familyLabel("anchor")).toMatch(/run these first/i);
  });

  it("falls back to the raw family name for an unknown one", () => {
    expect(familyLabel("something-new")).toBe("something-new");
  });
});

describe("toImportPayload", () => {
  it("carries the query and engine that produced the result", () => {
    const payload = toImportPayload('"Timotheous Samar" LinkedIn', "Bing", {
      url: "https://example.com/page",
      title: "Page",
      snippet: "Snippet",
      imageUrl: "",
      caption: "",
    });
    expect(payload).toMatchObject({
      query: '"Timotheous Samar" LinkedIn',
      engine: "Bing",
      url: "https://example.com/page",
    });
  });

  it("returns null when there is no URL, so nothing empty is sent", () => {
    expect(
      toImportPayload("q", "Google", {
        url: "   ",
        title: "",
        snippet: "",
        imageUrl: "",
        caption: "",
      }),
    ).toBeNull();
  });

  it("passes an image URL through as image evidence", () => {
    const payload = toImportPayload("q", "Google", {
      url: "https://example.org/page",
      title: "",
      snippet: "",
      imageUrl: "https://example.org/photo.jpg",
      caption: "Conference speakers",
    });
    expect(payload?.image_url).toBe("https://example.org/photo.jpg");
    expect(payload?.caption).toBe("Conference speakers");
  });

  it("sends null rather than an empty string for absent optional fields", () => {
    const payload = toImportPayload("q", "Google", {
      url: "https://example.org/page",
      title: "",
      snippet: "",
      imageUrl: "",
      caption: "",
    });
    expect(payload?.image_url).toBeNull();
    expect(payload?.caption).toBeNull();
  });
});

describe("filtering imported results", () => {
  it("separates image evidence from text results", () => {
    const results = [result(), result({ id: "2", is_image: true })];
    expect(imageEvidence(results).map((item) => item.id)).toEqual(["2"]);
  });

  it("picks out public social profile pages", () => {
    const results = [result(), result({ id: "2", url_kind: "web", platform: "example.org" })];
    expect(socialResults(results).map((item) => item.id)).toEqual(["1"]);
  });
});

describe("evidenceClassLabel", () => {
  it("says plainly that an imported result came from the investigator", () => {
    expect(evidenceClassLabel("investigator_imported")).toMatch(/imported by you/i);
  });

  it("distinguishes an API fetch from a page fetch", () => {
    expect(evidenceClassLabel("api_fetched")).not.toBe(evidenceClassLabel("page_fetched"));
  });

  it("falls back to the raw class rather than inventing a label", () => {
    expect(evidenceClassLabel("something_new")).toBe("something_new");
  });
});
