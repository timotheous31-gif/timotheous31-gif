/**
 * Types mirroring the backend's Pydantic schemas.
 *
 * Kept hand-written rather than generated so the dashboard depends on a small,
 * explicit surface: if the API changes shape, TypeScript fails the build here
 * rather than at runtime in a user's browser.
 */

export type CaseStatus = "NEW" | "RUNNING" | "PAUSED" | "COMPLETE" | "ARCHIVED";

export type TargetType =
  | "USERNAME"
  | "DOMAIN"
  | "EMAIL"
  | "ORGANIZATION"
  | "URL"
  | "IP"
  | "REPOSITORY"
  | "SOCIAL_PROFILE";

export type TargetStatus = "PENDING" | "RUNNING" | "COMPLETE" | "FAILED" | "SKIPPED";

export type RunStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCESS"
  | "PARTIAL"
  | "FAILED"
  | "SKIPPED"
  | "TIMEOUT";

export type JobState = "QUEUED" | "RUNNING" | "COMPLETE" | "FAILED" | "CANCELLED";

export type Classification = "PUBLIC" | "PERSONAL" | "SENSITIVE" | "RESTRICTED";

export type MatchStrength =
  | "LIKELY_MATCH"
  | "PROBABLE_MATCH"
  | "POSSIBLE_MATCH"
  | "WEAK_ASSOCIATION";

export type EntityType =
  | "PERSONA"
  | "USERNAME"
  | "EMAIL"
  | "DOMAIN"
  | "ORGANIZATION"
  | "WEBSITE"
  | "REPOSITORY"
  | "SOCIAL_ACCOUNT"
  | "IP_ADDRESS"
  | "CERTIFICATE"
  | "DOCUMENT";

export interface Tag {
  id: string;
  name: string;
}

export interface Case {
  id: string;
  name: string;
  description: string | null;
  status: CaseStatus;
  notes: string | null;
  tags: Tag[];
  created_at: string;
  updated_at: string;
}

export interface CaseSummary {
  case: Case;
  targets: number;
  findings: number;
  entities: number;
  relationships: number;
  evidence: number;
  timeline_events: number;
  collectors_run: string[];
  confidence_distribution: { high: number; medium: number; low: number };
  last_run_at: string | null;
}

export interface Target {
  id: string;
  case_id: string;
  type: TargetType;
  raw_input: string;
  normalized_value: string;
  status: TargetStatus;
  notes: string | null;
  attributes: Record<string, unknown>;
  tags: Tag[];
  created_at: string;
  updated_at: string;
}

export interface EvidenceRecord {
  id: string;
  collector: string;
  source_url: string | null;
  retrieved_at: string;
  sha256: string;
  content_type: string | null;
  size_bytes: number;
  excerpt: string | null;
  redacted: boolean;
  created_at: string;
  /** One artefact can support several findings, and vice versa. */
  finding_ids: string[];
}

export interface Finding {
  id: string;
  case_id: string;
  target_id: string | null;
  run_id: string | null;
  kind: string;
  title: string;
  summary: string | null;
  data: Record<string, unknown>;
  collector: string;
  source_url: string | null;
  confidence: number;
  confidence_reasons: string[];
  classification: Classification;
  redacted: boolean;
  observed_at: string | null;
  created_at: string;
  evidence: EvidenceRecord[];
}

export interface CollectorRun {
  id: string;
  target_id: string;
  collector: string;
  collector_version: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_type: string | null;
  error_message: string | null;
  stats: Record<string, unknown>;
}

export interface Entity {
  id: string;
  case_id: string;
  type: EntityType;
  display_name: string;
  canonical_value: string;
  aliases: string[];
  attributes: Record<string, unknown>;
  confidence: number;
  confidence_reasons: string[];
  notes: string | null;
  created_at: string;
  source_finding_ids: string[];
}

export interface Relationship {
  id: string;
  case_id: string;
  source_entity_id: string;
  target_entity_id: string;
  source_label: string | null;
  target_label: string | null;
  type: string;
  confidence: number;
  confidence_reasons: string[];
  strength: MatchStrength;
  collector: string;
  source_url: string | null;
  attributes: Record<string, unknown>;
  evidence_finding_ids: string[];
  created_at: string;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  confidence: number;
  attributes: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence: number;
  strength: string;
  reasons: string[];
  collector: string;
  evidence_ids: string[];
  attributes: Record<string, unknown>;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: {
    node_count: number;
    edge_count: number;
    entity_types: string[];
    relationship_types: string[];
  };
  summary: {
    node_count: number;
    edge_count: number;
    entity_type_counts: Record<string, number>;
    relationship_type_counts: Record<string, number>;
    component_count: number;
    largest_component_size: number;
    most_connected: { id: string; label: string; type: string; degree: number }[];
  };
}

export interface TimelineEvent {
  id: string;
  case_id: string;
  finding_id: string | null;
  entity_id: string | null;
  occurred_at: string;
  kind: string;
  title: string;
  description: string | null;
  collector: string;
  source_url: string | null;
  confidence: number;
  attributes: Record<string, unknown>;
}

export interface TimelineResponse {
  events: TimelineEvent[];
  summary: {
    event_count: number;
    first_event: string | null;
    last_event: string | null;
    span_days?: number;
    kinds: Record<string, number>;
  };
}

export interface Job {
  id: string;
  case_id: string;
  celery_id: string | null;
  state: JobState;
  progress: number;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_type: string | null;
  error_message: string | null;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  cancel_requested: boolean;
  created_at: string;
}

export interface RunResponse {
  job: Job;
  dispatch: Record<string, unknown>;
}

export interface CollectorInfo {
  name: string;
  version: string;
  description: string;
  supported_targets: string[];
  requires_api_key: boolean;
  rate_limit: string;
  timeout: number;
  run_timeout: number | null;
  source_attribution: string;
  network: boolean;
  available: boolean;
  unavailable_reason: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  detail?: unknown;
  request_id?: string | null;
}

export interface EvidenceVerification {
  total: number;
  verified: number;
  missing_raw: number;
  mismatched: string[];
  intact: boolean;
}
