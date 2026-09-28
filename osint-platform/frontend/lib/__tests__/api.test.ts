import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError, api, buildUrl, filenameFrom } from "@/lib/api";

type FetchInit = {
  method?: string;
  headers: Record<string, string>;
  credentials?: RequestCredentials;
};

/** The RequestInit of the most recent fetch call. */
function lastInit(mock: { mock: { calls: unknown[][] } }): FetchInit {
  const call = mock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return call[1] as FetchInit;
}

/** The URL of the most recent fetch call. */
function lastUrl(mock: { mock: { calls: unknown[][] } }): string {
  const call = mock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return String(call[0]);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("buildUrl", () => {
  it("prefixes the API version", () => {
    expect(buildUrl("/cases")).toContain("/api/v1/cases");
  });

  it("drops empty query values so filters can be cleared", () => {
    const url = buildUrl("/cases", { q: "", status: undefined, limit: 10 });
    expect(url).toContain("limit=10");
    expect(url).not.toContain("q=");
    expect(url).not.toContain("status=");
  });

  it("repeats array parameters", () => {
    const url = buildUrl("/cases/1/graph", { types: ["DOMAIN", "IP_ADDRESS"] });
    expect(url).toContain("types=DOMAIN");
    expect(url).toContain("types=IP_ADDRESS");
  });
});

describe("error handling", () => {
  it("raises ApiError carrying the backend's stable code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ code: "not_found", message: "Case does not exist" }, 404)),
    );

    await expect(api.getCase("missing")).rejects.toMatchObject({
      name: "ApiError",
      code: "not_found",
      status: 404,
    });

    try {
      await api.getCase("missing");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isNotFound).toBe(true);
    }
  });

  it("falls back to a status-derived envelope for non-JSON errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("upstream exploded", { status: 502 })));
    await expect(api.getCase("x")).rejects.toMatchObject({ code: "http_502" });
  });

  it("distinguishes an unreachable API from an API error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));
    await expect(api.listCases()).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("requests", () => {
  it("returns parsed JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [], total: 0, limit: 50, offset: 0 })),
    );
    await expect(api.listCases()).resolves.toEqual({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
  });

  it("handles 204 responses from deletes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(api.deleteCase("id")).resolves.toBeUndefined();
  });

  it("sends a JSON content type only when there is a body", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ id: "1" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.createCase({ name: "Example" });
    const postInit = lastInit(fetchMock);
    expect(postInit.headers["Content-Type"]).toBe("application/json");
    expect(postInit.method).toBe("POST");

    fetchMock.mockClear();
    await api.getCase("1");
    expect(lastInit(fetchMock).headers["Content-Type"]).toBeUndefined();
  });

  /**
   * The bug: an authenticated report request answered 401.
   *
   * The endpoint was fine. The page fetched it with a bare `fetch(url)`, which
   * defaults to `credentials: "same-origin"` — so across the :3000/:8000 split
   * the session cookie was never sent. Everything else on the page went through
   * `api` and worked, which is why only the report failed.
   *
   * These assert the property that was missing rather than the symptom: the
   * report goes through the same credentialed path as every other call.
   */
  describe("the report is fetched like every other authenticated call", () => {
    function reportResponse(body = "# Report", type = "text/markdown"): Response {
      return new Response(body, {
        status: 200,
        headers: {
          "content-type": type,
          "content-disposition": 'attachment; filename="example-case-report.md"',
        },
      });
    }

    it("sends the session cookie", async () => {
      const fetchMock = vi.fn(async () => reportResponse());
      vi.stubGlobal("fetch", fetchMock);

      await api.report("case-1", "md");

      // The one line that was missing. `same-origin` — fetch's default — is the
      // bug; anything else here means no cookie crosses the origin split.
      expect(lastInit(fetchMock).credentials).toBe("include");
    });

    it("requests the format and the classification filter that were chosen", async () => {
      const fetchMock = vi.fn(async () => reportResponse());
      vi.stubGlobal("fetch", fetchMock);

      await api.report("case-1", "json", { max_classification: "SENSITIVE" });

      const url = lastUrl(fetchMock);
      expect(url).toContain("/cases/case-1/report");
      expect(url).toContain("format=json");
      expect(url).toContain("max_classification=SENSITIVE");
    });

    it("works for markdown, HTML and JSON alike", async () => {
      for (const [format, type] of [
        ["md", "text/markdown"],
        ["html", "text/html"],
        ["json", "application/json"],
      ] as const) {
        const fetchMock = vi.fn(async () => reportResponse("body", type));
        vi.stubGlobal("fetch", fetchMock);

        const document_ = await api.report("case-1", format);

        expect(document_.body).toBe("body");
        expect(document_.contentType).toBe(type);
        expect(lastInit(fetchMock).credentials).toBe("include");
      }
    });

    it("raises the API's own error rather than a bare status", async () => {
      // The old code threw `Error("Report request failed (401)")`, which the
      // session provider cannot recognise — so an expired session showed a
      // broken panel instead of the login screen.
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(JSON.stringify({ code: "authentication_required", message: "Sign in" }), {
              status: 401,
              headers: { "content-type": "application/json" },
            }),
        ),
      );

      await expect(api.report("case-1", "md")).rejects.toBeInstanceOf(ApiError);
      await expect(api.report("case-1", "md")).rejects.toMatchObject({
        status: 401,
        code: "authentication_required",
      });
    });

    it("keeps the filename the server asked for", async () => {
      vi.stubGlobal("fetch", vi.fn(async () => reportResponse()));

      expect((await api.report("case-1", "md")).filename).toBe("example-case-report.md");
    });

    it("can request the investigator-facing dossier the backend serves", async () => {
      // It shipped unreachable: the backend registered `dossier`, and the
      // client's format union did not include it, so no UI could ask for it.
      const fetchMock = vi.fn(async () => reportResponse("<html>", "text/html"));
      vi.stubGlobal("fetch", fetchMock);

      const document_ = await api.report("case-1", "dossier", {
        max_classification: "PERSONAL",
      });

      expect(lastUrl(fetchMock)).toContain("format=dossier");
      expect(lastInit(fetchMock).credentials).toBe("include");
      expect(document_.contentType).toBe("text/html");
    });

    it("cannot be reached through a URL-only helper any more", () => {
      // `reportUrl` returned a bare string, which invited exactly one mistake:
      // handing it to `fetch`, an anchor or `window.open`, none of which carry
      // the session. Removing it removes the mistake.
      expect("reportUrl" in api).toBe(false);
    });
  });

  describe("filenameFrom", () => {
    it("reads the name out of a Content-Disposition header", () => {
      expect(filenameFrom('attachment; filename="a-report.md"', "x")).toBe("a-report.md");
      expect(filenameFrom("attachment; filename=a-report.json", "x")).toBe("a-report.json");
    });

    it("falls back when the server sent nothing usable", () => {
      expect(filenameFrom(null, "case-report.md")).toBe("case-report.md");
      expect(filenameFrom("attachment", "case-report.md")).toBe("case-report.md");
    });

    it("can only ever produce a name, never a path", () => {
      // The value comes from a response header and ends up in a download.
      expect(filenameFrom('attachment; filename="../../etc/passwd"', "safe")).toBe("passwd");
      expect(filenameFrom('attachment; filename="/tmp/evil.md"', "safe")).toBe("evil.md");
      expect(filenameFrom('attachment; filename=".."', "safe")).toBe("safe");
    });
  });

  it("omits the type from a preview when none was chosen", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ambiguous: false }));
    vi.stubGlobal("fetch", fetchMock);

    await api.previewTarget("case-1", "example.com");
    const body = JSON.parse((lastInit(fetchMock) as unknown as { body: string }).body);
    expect(body).toEqual({ value: "example.com" });
  });

  it("sends the chosen type so a name resolves to PERSON or ORGANIZATION", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ambiguous: false, type: "PERSON" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.previewTarget("case-1", "Timotheous Samar", "PERSON");
    const body = JSON.parse((lastInit(fetchMock) as unknown as { body: string }).body);
    expect(body).toEqual({ value: "Timotheous Samar", type: "PERSON" });
  });

  it("sends the chosen type when adding a target", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ id: "1" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.addTarget("case-1", { value: "Timotheous Samar", type: "PERSON" });
    const body = JSON.parse((lastInit(fetchMock) as unknown as { body: string }).body);
    expect(body).toEqual({ value: "Timotheous Samar", type: "PERSON" });
  });
});
