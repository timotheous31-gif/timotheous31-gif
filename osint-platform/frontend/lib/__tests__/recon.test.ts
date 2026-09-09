import { describe, expect, it } from "vitest";

import {
  EXPANDED_BY_DEFAULT,
  FAMILY_ORDER,
  SEARCH_ENGINES,
  capabilitySummary,
  evidenceClassLabel,
  familyLabel,
  familyPurpose,
  groupQueries,
  groupSearchUrls,
  orderedGroups,
  imageEvidence,
  searchUrl,
  socialResults,
  toImportPayload,
} from "@/lib/recon";
import type { ImportedResult, ReconQuery, SourcePlatform } from "@/types/api";

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

describe("grouped recon layout", () => {
  it("orders families narrowest-first so a list is worked top to bottom", () => {
    const groups = orderedGroups([
      query({ query: "a", family: "general" }),
      query({ query: "b", family: "anchor" }),
      query({ query: "c", family: "image" }),
      query({ query: "d", family: "social" }),
    ]);
    expect(groups.map(([family]) => family)).toEqual(["anchor", "social", "image", "general"]);
  });

  it("drops families with no queries rather than showing empty sections", () => {
    const groups = orderedGroups([query({ family: "anchor" })]);
    expect(groups).toHaveLength(1);
  });

  it("keeps an unknown family rather than silently discarding it", () => {
    const groups = orderedGroups([query({ family: "something-new" })]);
    expect(groups.map(([family]) => family)).toContain("something-new");
  });

  it("expands the narrowest families and collapses the rest", () => {
    expect(EXPANDED_BY_DEFAULT.has("anchor")).toBe(true);
    expect(EXPANDED_BY_DEFAULT.has("social")).toBe(true);
    expect(EXPANDED_BY_DEFAULT.has("general")).toBe(false);
  });

  it("explains what each family is for", () => {
    for (const family of FAMILY_ORDER) expect(familyPurpose(family)).toBeTruthy();
  });

  it("says image queries are not a reverse image search", () => {
    expect(familyPurpose("image")).toMatch(/not a reverse image search/i);
  });

  it("builds one search link per query for opening a whole group", () => {
    const urls = groupSearchUrls([query({ query: "a" }), query({ query: "b" })]);
    expect(urls).toHaveLength(2);
    expect(urls[0]).toContain("google.com/search");
  });
});

describe("search engines are navigation only", () => {
  it("offers Google, Google Images, Bing and DuckDuckGo", () => {
    const keys = SEARCH_ENGINES.map((engine) => engine.key);
    expect(keys).toContain("Google");
    expect(keys).toContain("Google Images");
    expect(keys).toContain("Bing");
    expect(keys).toContain("DuckDuckGo");
  });

  it("builds an image search URL rather than a web one", () => {
    expect(searchUrl("example", "Google Images")).toContain("tbm=isch");
  });

  it("percent-encodes the whole query, operators included", () => {
    const url = searchUrl('"Example Person" site:linkedin.com/in');
    expect(url).toBe(
      "https://www.google.com/search?q=%22Example%20Person%22%20site%3Alinkedin.com%2Fin",
    );
    // A link the investigator follows. Nothing here fetches anything.
    expect(url.startsWith("https://www.google.com/search?q=")).toBe(true);
  });

  it("falls back to Google rather than building a broken link", () => {
    // @ts-expect-error - deliberately passing an engine that does not exist
    expect(searchUrl("example", "Nonexistent")).toContain("google.com/search");
  });
});

describe("handle queries", () => {
  it("groups handle searches into their own family, expanded by default", () => {
    expect(FAMILY_ORDER).toContain("handle");
    expect(EXPANDED_BY_DEFAULT.has("handle")).toBe(true);
    expect(familyLabel("handle")).toBe("Handles");
    expect(familyPurpose("handle")).toContain("never proof of the same owner");
  });
});

describe("capabilitySummary", () => {
  const platform = (overrides: Partial<SourcePlatform> = {}): SourcePlatform => ({
    platform: "linkedin",
    display_name: "LinkedIn",
    domains: ["linkedin.com"],
    server_fetchable: false,
    public_api_available: false,
    manual_search_supported: true,
    handle_check_supported: false,
    image_reference_supported: false,
    search_filters: ["linkedin.com/in"],
    notes: null,
    ...overrides,
  });

  it("says a blocked platform is a boundary, not a gap", () => {
    expect(capabilitySummary(platform())).toContain("Search it yourself");
  });

  it("prefers the platform's own explanation when it has one", () => {
    expect(capabilitySummary(platform({ notes: "Refuses anonymous requests." }))).toBe(
      "Refuses anonymous requests.",
    );
  });

  it("says plainly when a handle is checked directly", () => {
    expect(
      capabilitySummary(platform({ handle_check_supported: true, server_fetchable: true })),
    ).toContain("checked directly");
  });
});

describe("import payload", () => {
  it("carries the handle and displayed name the investigator saw", () => {
    const payload = toImportPayload("q", "Google", {
      url: "https://example.org/staff",
      title: "",
      snippet: "",
      imageUrl: "",
      caption: "",
      handle: " example-person ",
      displayName: " Example Person Dass ",
      notes: " same employer ",
    });
    expect(payload).toMatchObject({
      handle: "example-person",
      display_name: "Example Person Dass",
      notes: "same employer",
    });
  });

  it("sends null rather than empty strings for optional fields", () => {
    const payload = toImportPayload("q", "Google", {
      url: "https://example.org/staff",
      title: "",
      snippet: "",
      imageUrl: "",
      caption: "",
    });
    expect(payload?.handle).toBeNull();
    expect(payload?.display_name).toBeNull();
  });
});
