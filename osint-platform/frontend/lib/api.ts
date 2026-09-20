/**
 * Typed client for the OSINT platform API.
 *
 * One place builds every request, so the base URL, error envelope and query
 * encoding are handled consistently. Errors are thrown as `ApiError`, which
 * carries the backend's stable error code — the UI can then distinguish "this
 * case does not exist" from "the API is unreachable".
 */

import type {
  MfaEnabled,
  MfaEnrollment,
  MfaStatus,
  AnalystDecisionRecord,
  AnalystDecisionValue,
  CandidateGroup,
  DecisionSubject,
  ImageEvidenceRecord,
  PublicContactRecord,
  SocialProfileRecord,
  ApiErrorBody,
  AuditEntry,
  Case,
  CaseSummary,
  CollectorInfo,
  CollectorRun,
  Entity,
  EvidenceRecord,
  EvidenceVerification,
  Finding,
  GraphResponse,
  ImportedResult,
  Job,
  ManualResultInput,
  NormalizationPreview,
  Page,
  PersonContext,
  ReconQueryPlan,
  SearchIngestResult,
  StagedReconPlan,
  Membership,
  Relationship,
  RunResponse,
  SessionInfo,
  Target,
  TargetType,
  TimelineResponse,
  WorkspaceSummary,
} from "@/types/api";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const PREFIX = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.detail = body.detail;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export class NetworkError extends Error {
  constructor(cause: unknown) {
    super(
      "Could not reach the API. Check that the backend is running and that " +
        `NEXT_PUBLIC_API_URL (${API_BASE}) is correct.`,
    );
    this.name = "NetworkError";
    this.cause = cause;
  }
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

/**
 * The report formats the API serves.
 *
 * `dossier` is the investigator/client-facing document — the same report model
 * as the other three, arranged for a reader. Kept in one place so a format the
 * backend serves cannot be unreachable from the interface, which is how
 * `dossier` shipped invisible.
 */
export type ReportRenderFormat = "dossier" | "html" | "md" | "json";

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${PREFIX}${path}`, `${API_BASE}/`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

/**
 * Read the CSRF token the API set alongside the session.
 *
 * The session cookie itself is `HttpOnly` and deliberately unreadable here — that
 * is what stops a cross-site-scripting bug from becoming an account takeover. The
 * CSRF cookie is readable precisely because it has to be copied into a header,
 * and on its own it authorises nothing.
 */
export function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)osint_csrf=([^;]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

/** Methods the API requires a CSRF token on. */
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Called when the API answers 401. Set by the session provider so the client
 * stays free of React imports; a module-level hook is simpler than threading a
 * callback through every call site.
 */
let onUnauthenticated: (() => void) | null = null;

export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

/**
 * Send one authenticated request and hand back the raw `Response`.
 *
 * The single place that decides *how* this app talks to its API: the session
 * cookie, the CSRF header, the cache policy. Both `request` and
 * `requestDocument` go through it, so a caller cannot accidentally reach the
 * API without credentials by picking the wrong helper — which is exactly how
 * the report preview came to 401 against a perfectly healthy endpoint.
 */
async function send(path: string, options: RequestInit & { query?: Query } = {}): Promise<Response> {
  const { query, ...init } = options;
  const method = (init.method ?? "GET").toUpperCase();
  try {
    return await fetch(buildUrl(path, query), {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(UNSAFE.has(method) ? { "X-CSRF-Token": csrfToken() } : {}),
        ...init.headers,
      },
      // The session is a cookie, so it has to be sent on cross-origin calls —
      // which is the normal development setup (:3000 talking to :8000). The API
      // allows credentials only from origins it lists explicitly.
      //
      // `fetch` defaults to `credentials: "same-origin"`, which sends nothing
      // across that split. This line is the difference between a request that
      // is authenticated and one that is not.
      credentials: "include",
      cache: "no-store",
    });
  } catch (cause) {
    throw new NetworkError(cause);
  }
}

/** Raise the API's error envelope for a failed response. */
async function refuse(response: Response, text: string): Promise<never> {
  let body: ApiErrorBody = { code: `http_${response.status}`, message: text || "Request failed" };
  try {
    body = { ...body, ...(JSON.parse(text) as ApiErrorBody) };
  } catch {
    // The body was not JSON; the status-derived envelope above stands.
  }
  throw new ApiError(response.status, body);
}

async function request<T>(
  path: string,
  options: RequestInit & { query?: Query } = {},
): Promise<T> {
  const response = await send(path, options);

  if (response.status === 401 && !path.startsWith("/auth/")) {
    // The session expired or was revoked. Tell the provider so the app can show
    // the login screen instead of a page full of failed panels.
    onUnauthenticated?.();
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!response.ok) await refuse(response, text);

  if (!text) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("json")) return text as unknown as T;
  return JSON.parse(text) as T;
}

/** A document the API served, with the name it asked for it to be saved under. */
export interface ApiDocument {
  body: string;
  /** From `Content-Disposition`, so the server names the file, not the browser. */
  filename: string;
  contentType: string;
}

/** The server's suggested filename, or `fallback` when it did not send one. */
export function filenameFrom(disposition: string | null, fallback: string): string {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition ?? "");
  const name = match?.[1]?.trim();
  if (!name) return fallback;
  // A filename arrives from the server and ends up in a download. Path
  // separators and traversal segments are stripped so it can only ever be a
  // name, never a location.
  const bare = name.split(/[\\/]/).pop() ?? "";
  const safe = bare.replace(/[\u0000-\u001f]/g, "").replace(/^\.+/, "").trim();
  return safe || fallback;
}

/**
 * Fetch a document — a report, an export — through the authenticated client.
 *
 * Exists because the alternatives all lose the session. A bare `fetch` defaults
 * to `credentials: "same-origin"` and sends no cookie across the :3000/:8000
 * split; an `<a href>` or `window.open` leaves the SPA entirely and, on a
 * genuinely cross-site deployment, a `SameSite=lax` cookie does not follow it.
 * Both produce a 401 from an endpoint that is working correctly.
 *
 * Every report format the API serves is text, so this reads text and leaves the
 * caller to render it or wrap it in a `Blob` to save.
 */
export async function requestDocument(
  path: string,
  options: RequestInit & { query?: Query } = {},
  fallbackName = "download",
): Promise<ApiDocument> {
  const response = await send(path, options);

  if (response.status === 401 && !path.startsWith("/auth/")) {
    onUnauthenticated?.();
  }

  const body = await response.text();
  if (!response.ok) await refuse(response, body);

  return {
    body,
    filename: filenameFrom(response.headers.get("content-disposition"), fallbackName),
    contentType: response.headers.get("content-type") ?? "application/octet-stream",
  };
}

/**
 * Hand a document to the browser as a download.
 *
 * The object URL is revoked immediately: it is a readable handle on the
 * investigation file for as long as it exists, and the download has already
 * taken its copy.
 */
export function saveDocument(document_: ApiDocument): void {
  const blob = new Blob([document_.body], { type: document_.contentType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = document_.filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export const api = {
  health: () => request<{ status: string; version: string; environment: string }>("/../../health"),

  // --- authentication ------------------------------------------------------
  login: (email: string, password: string) =>
    request<SessionInfo>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<SessionInfo>("/auth/me"),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // --- two-factor authentication -------------------------------------------
  //
  // `mfaEnroll` is the only call in this module whose response contains a
  // secret. Nothing here stores one: the value is passed to a component, held
  // in React state for the length of the enrolment, and gone on unmount.
  mfaStatus: () => request<MfaStatus>("/auth/mfa"),
  mfaEnroll: (password: string) =>
    request<MfaEnrollment>("/auth/mfa/enroll", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  mfaConfirm: (code: string) =>
    request<MfaEnabled>("/auth/mfa/confirm", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  mfaVerify: (answer: { code?: string; recovery_code?: string }) =>
    request<SessionInfo>("/auth/mfa/verify", {
      method: "POST",
      body: JSON.stringify(answer),
    }),
  mfaDisable: (password: string) =>
    request<void>("/auth/mfa/disable", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  // --- workspaces ----------------------------------------------------------
  listWorkspaces: () => request<WorkspaceSummary[]>("/workspaces"),
  listMembers: (workspaceId: string) =>
    request<Membership[]>(`/workspaces/${workspaceId}/members`),
  readAudit: (workspaceId: string, query?: Query) =>
    request<AuditEntry[]>(`/workspaces/${workspaceId}/audit`, { query }),

  listCases: (query?: Query) => request<Page<Case>>("/cases", { query }),
  getCase: (id: string) => request<Case>(`/cases/${id}`),
  caseSummary: (id: string) => request<CaseSummary>(`/cases/${id}/summary`),
  createCase: (payload: {
    name: string;
    description?: string;
    tags?: string[];
    /** Required when the signed-in user belongs to more than one workspace. */
    workspace_id?: string;
  }) =>
    request<Case>("/cases", { method: "POST", body: JSON.stringify(payload) }),
  updateCase: (id: string, payload: Record<string, unknown>) =>
    request<Case>(`/cases/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCase: (id: string) => request<void>(`/cases/${id}`, { method: "DELETE" }),

  listTargets: (caseId: string, query?: Query) =>
    request<Page<Target>>(`/cases/${caseId}/targets`, { query }),
  addTarget: (
    caseId: string,
    payload: { value: string; type?: TargetType | null; context?: PersonContext | null },
  ) =>
    request<Target>(`/cases/${caseId}/targets`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteTarget: (caseId: string, targetId: string) =>
    request<void>(`/cases/${caseId}/targets/${targetId}`, { method: "DELETE" }),
  /**
   * Ask what the backend would store. Name-shaped input comes back with
   * `ambiguous: true` and the candidate types rather than as an error.
   */
  previewTarget: (caseId: string, value: string, type?: TargetType | null) =>
    request<NormalizationPreview>(`/cases/${caseId}/targets/preview`, {
      method: "POST",
      body: JSON.stringify(type ? { value, type } : { value }),
    }),

  /**
   * The searches to run by hand. The platform generates them and never submits
   * them anywhere — see the plan's `execution` note.
   */
  reconQueries: (caseId: string, targetId: string) =>
    request<ReconQueryPlan>(`/cases/${caseId}/targets/${targetId}/recon-queries`),
  /** The staged plan: variants, then anchors, then the broad sweeps. */
  reconPlan: (caseId: string, targetId: string) =>
    request<StagedReconPlan>(`/cases/${caseId}/targets/${targetId}/recon-plan`),
  /**
   * Run the plan through a configured search provider and ingest the results.
   *
   * With no provider configured this returns `configured: false` and does
   * nothing — which the UI must show as "not searched", never as "found
   * nothing".
   */
  runProviderSearch: (caseId: string, targetId: string) =>
    request<SearchIngestResult>(`/cases/${caseId}/targets/${targetId}/search`, {
      method: "POST",
    }),
  importReconResults: (caseId: string, targetId: string, results: ManualResultInput[]) =>
    request<ImportedResult[]>(`/cases/${caseId}/targets/${targetId}/recon-results`, {
      method: "POST",
      body: JSON.stringify({ results }),
    }),
  listSocialProfiles: (caseId: string, query?: Query) =>
    request<SocialProfileRecord[]>(`/cases/${caseId}/social-profiles`, { query }),
  listPublicContacts: (caseId: string, query?: Query) =>
    request<PublicContactRecord[]>(`/cases/${caseId}/public-contacts`, { query }),
  listImages: (caseId: string, query?: Query) =>
    request<ImageEvidenceRecord[]>(`/cases/${caseId}/images`, { query }),
  fetchImage: (caseId: string, imageId: string) =>
    request<ImageEvidenceRecord>(`/cases/${caseId}/images/${imageId}/fetch`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  listCandidateGroups: (caseId: string) =>
    request<CandidateGroup[]>(`/cases/${caseId}/candidates`),
  recordDecision: (
    caseId: string,
    payload: {
      subject_type: DecisionSubject;
      subject_id: string;
      decision: AnalystDecisionValue;
      note?: string | null;
    },
  ) =>
    request<AnalystDecisionRecord>(`/cases/${caseId}/decisions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  clearDecision: (caseId: string, subjectType: DecisionSubject, subjectId: string) =>
    request<void>(`/cases/${caseId}/decisions/${subjectType}/${subjectId}`, { method: "DELETE" }),

  listReconResults: (caseId: string, query?: Query) =>
    request<ImportedResult[]>(`/cases/${caseId}/recon-results`, { query }),

  runInvestigation: (caseId: string, payload: Record<string, unknown> = {}) =>
    request<RunResponse>(`/cases/${caseId}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listCaseJobs: (caseId: string) => request<Job[]>(`/cases/${caseId}/jobs`),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),

  listFindings: (caseId: string, query?: Query) =>
    request<Page<Finding>>(`/cases/${caseId}/findings`, { query }),
  listEvidence: (caseId: string, query?: Query) =>
    request<Page<EvidenceRecord>>(`/cases/${caseId}/evidence`, { query }),
  verifyEvidence: (caseId: string) =>
    request<EvidenceVerification>(`/cases/${caseId}/evidence/verify`),
  listRuns: (caseId: string) => request<CollectorRun[]>(`/cases/${caseId}/runs`),

  listEntities: (caseId: string, query?: Query) =>
    request<Page<Entity>>(`/cases/${caseId}/entities`, { query }),
  listRelationships: (caseId: string, query?: Query) =>
    request<Page<Relationship>>(`/cases/${caseId}/relationships`, { query }),
  graph: (caseId: string, query?: Query) =>
    request<GraphResponse>(`/cases/${caseId}/graph`, { query }),
  timeline: (caseId: string, query?: Query) =>
    request<TimelineResponse>(`/cases/${caseId}/timeline`, { query }),

  collectors: () => request<CollectorInfo[]>("/collectors"),

  /**
   * An investigation report, through the authenticated client.
   *
   * There is deliberately no `reportUrl` helper any more. It returned a bare
   * string, which invited exactly one mistake — handing it to `fetch`, an
   * `<a href>` or `window.open`, none of which carry the session — and that
   * mistake is what made an authenticated report request answer 401.
   */
  report: (
    caseId: string,
    format: ReportRenderFormat,
    query: Query = {},
  ): Promise<ApiDocument> =>
    requestDocument(
      `/cases/${caseId}/report`,
      { query: { format, ...query } },
      `case-report.${format}`,
    ),
};

export { buildUrl };
