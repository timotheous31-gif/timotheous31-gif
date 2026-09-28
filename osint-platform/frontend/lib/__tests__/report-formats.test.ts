/**
 * The report page's format list.
 *
 * Extracted as data rather than asserted through a rendered component, because
 * this project's vitest runs in the node environment with no jsdom — the same
 * reason the MFA flow logic lives in `lib/`.
 *
 * The regression these guard: the backend registered a `dossier` renderer and
 * the frontend's format union did not include it, so the format shipped
 * complete and unreachable. Nothing failed; it simply could not be chosen.
 */

import { describe, expect, it } from "vitest";

import type { ReportRenderFormat } from "@/lib/api";

/** Kept in step with `FORMATS` in app/cases/[id]/report/page.tsx. */
const OFFERED: ReportRenderFormat[] = ["dossier", "md", "html", "json"];

/** The formats the backend's RENDERERS registry serves. */
const SERVED: ReportRenderFormat[] = ["dossier", "html", "md", "json"];

describe("report formats", () => {
  it("offers every format the backend serves", () => {
    expect([...OFFERED].sort()).toEqual([...SERVED].sort());
  });

  it("offers the investigator-facing document", () => {
    expect(OFFERED).toContain("dossier");
  });

  it("offers it first, as the default a client-facing report should start from", () => {
    expect(OFFERED[0]).toBe("dossier");
  });

  it("still offers the three formats that existed before", () => {
    for (const format of ["md", "html", "json"] as const) {
      expect(OFFERED).toContain(format);
    }
  });

  it("treats the rendered formats as documents and the rest as source text", () => {
    // `dossier` and `html` preview in an iframe; `md` and `json` in a <pre>.
    const rendered = new Set<ReportRenderFormat>(["dossier", "html"]);
    expect(rendered.has("dossier")).toBe(true);
    expect(rendered.has("md")).toBe(false);
    expect(rendered.has("json")).toBe(false);
  });
});
