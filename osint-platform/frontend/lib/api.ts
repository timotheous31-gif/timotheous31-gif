/**
 * Typed client for the OSINT platform API.
 *
 * One place builds every request, so the base URL, error envelope and query
 * encoding are handled consistently. Errors are thrown as `ApiError`, which
 * carries the backend's stable error code — the UI can then distinguish "this
 * case does not exist" from "the API is unreachable".
 */

import type {
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

async function request<T>(
  path: string,
  options: RequestInit & { query?: Query } = {},
): Promise<T> {
  const { query, ...init } = options;
  const method = (init.method ?? "GET").toUpperCase();
  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
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
      credentials: "include",
      cache: "no-store",
    });
  } catch (cause) {
    throw new NetworkError(cause);
  }

  if (response.status === 401 && !path.startsWith("/auth/")) {
    // The session expired or was revoked. Tell the provider so the app can show
    // the login screen instead of a page full of failed panels.
    onUnauthenticated?.();
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!response.ok) {
    let body: ApiErrorBody = { code: `http_${response.status}`, message: text || "Request failed" };
    try {
      body = { ...body, ...(JSON.parse(text) as ApiErrorBody) };
    } catch {
      // The body was not JSON; the status-derived envelope above stands.
    }
    throw new ApiError(response.status, body);
  }

  if (!text) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("json")) return text as unknown as T;
  return JSON.parse(text) as T;
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

  reportUrl: (caseId: string, format: "html" | "md" | "json") =>
    buildUrl(`/cases/${caseId}/report`, { format }),
  report: (caseId: string, format: "html" | "md" | "json") =>
    request<string>(`/cases/${caseId}/report`, { query: { format } }),
};

export { buildUrl };
