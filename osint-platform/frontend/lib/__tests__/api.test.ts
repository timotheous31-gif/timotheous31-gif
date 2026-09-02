import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError, api, buildUrl } from "@/lib/api";

type FetchInit = { method?: string; headers: Record<string, string> };

/** The RequestInit of the most recent fetch call. */
function lastInit(mock: { mock: { calls: unknown[][] } }): FetchInit {
  const call = mock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return call[1] as FetchInit;
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

  it("builds report URLs for each format", () => {
    expect(api.reportUrl("case-1", "html")).toContain("format=html");
    expect(api.reportUrl("case-1", "json")).toContain("/cases/case-1/report");
  });
});
