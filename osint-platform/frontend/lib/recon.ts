/**
 * Recon helpers: opening a query in a real search engine, and reading imports.
 *
 * The platform never submits a query itself, so the only thing the browser does
 * is build a link the investigator clicks. That is the whole mechanism, and
 * keeping it in one small pure function makes it obvious there is nothing else
 * going on.
 */

import type { ImportedResult, ManualResultInput, ReconQuery } from "@/types/api";

/** Search engines an investigator can open a generated query in. */
export const SEARCH_ENGINES = [
  { key: "Google", url: "https://www.google.com/search?q=" },
  { key: "Bing", url: "https://www.bing.com/search?q=" },
  { key: "DuckDuckGo", url: "https://duckduckgo.com/?q=" },
  { key: "Startpage", url: "https://www.startpage.com/sp/search?query=" },
] as const;

export type EngineKey = (typeof SEARCH_ENGINES)[number]["key"];

/**
 * The URL that opens `query` in `engine`.
 *
 * Returns a link for the investigator to follow in their own browser, under
 * their own session. Nothing is fetched here.
 */
export function searchUrl(query: string, engine: EngineKey = "Google"): string {
  const chosen = SEARCH_ENGINES.find((item) => item.key === engine) ?? SEARCH_ENGINES[0];
  return `${chosen.url}${encodeURIComponent(query)}`;
}

/** Group generated queries by family, preserving the backend's priority order. */
export function groupQueries(queries: ReconQuery[]): Map<string, ReconQuery[]> {
  const grouped = new Map<string, ReconQuery[]>();
  for (const query of queries) {
    grouped.set(query.family, [...(grouped.get(query.family) ?? []), query]);
  }
  return grouped;
}

/** Human label for a query family. */
export function familyLabel(family: string): string {
  const labels: Record<string, string> = {
    anchor: "Anchored — narrowest, run these first",
    general: "Web",
    social: "Social platforms",
    image: "Images",
    academic: "Professional & academic",
    document: "Documents",
  };
  return labels[family] ?? family;
}

/** One line on what a family is for, shown under its heading. */
export function familyPurpose(family: string): string {
  const purposes: Record<string, string> = {
    anchor:
      "Built from the anchors you supplied. These return the fewest strangers, so run them first.",
    general: "The plain name search — broad, and mostly other people.",
    social: "Public profile pages on each platform.",
    image: "Pages that publish a photograph alongside the name. Not a reverse image search.",
    academic: "Publications, registries and institutional pages.",
    document: "Public documents that mention the name.",
  };
  return purposes[family] ?? "";
}

/**
 * The order families are shown in: narrowest first.
 *
 * A recon list is worked top to bottom, so the queries most likely to return
 * the subject rather than a stranger belong at the top.
 */
export const FAMILY_ORDER = ["anchor", "social", "image", "academic", "general", "document"];

/** Families that start expanded. The rest collapse, to end the wall of cards. */
export const EXPANDED_BY_DEFAULT = new Set(["anchor", "social"]);

/** Group queries into display order, dropping empty families. */
export function orderedGroups(queries: ReconQuery[]): [string, ReconQuery[]][] {
  const grouped = groupQueries(queries);
  const known = FAMILY_ORDER.filter((family) => grouped.has(family));
  const rest = [...grouped.keys()].filter((family) => !FAMILY_ORDER.includes(family));
  return [...known, ...rest].map((family) => [family, grouped.get(family) ?? []]);
}

/** Search URLs for every query in a group, for "open all". */
export function groupSearchUrls(queries: ReconQuery[], engine: EngineKey = "Google"): string[] {
  return queries.map((query) => searchUrl(query.query, engine));
}

/**
 * Turn a pasted result into an import payload.
 *
 * Returns null when there is nothing usable, so the caller can leave the row
 * out rather than sending an empty record the backend would reject.
 */
export function toImportPayload(
  query: string,
  engine: string,
  row: { url: string; title: string; snippet: string; imageUrl: string; caption: string },
): ManualResultInput | null {
  const url = row.url.trim();
  if (!url) return null;
  return {
    query,
    url,
    title: row.title.trim(),
    snippet: row.snippet.trim(),
    engine,
    image_url: row.imageUrl.trim() || null,
    caption: row.caption.trim() || null,
  };
}

/** Imported results that carry an image. */
export function imageEvidence(results: ImportedResult[]): ImportedResult[] {
  return results.filter((result) => result.is_image);
}

/** Imported results that are public social or profile pages. */
export function socialResults(results: ImportedResult[]): ImportedResult[] {
  return results.filter((result) => result.url_kind === "social");
}

/**
 * How an imported result was obtained, in words.
 *
 * The UI shows this next to every result because the evidence classes are not
 * equally strong and the difference is easy to forget once a page is in a list.
 */
export function evidenceClassLabel(evidenceClass: string): string {
  const labels: Record<string, string> = {
    api_fetched: "Fetched from a public API by the platform",
    page_fetched: "Public page fetched by the platform",
    investigator_imported: "Imported by you from your own search",
  };
  return labels[evidenceClass] ?? evidenceClass;
}
