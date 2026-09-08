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
  /** A named natural person. Never inferred — the investigator selects it. */
  | "PERSON"
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

/** What the UI shows. Adds the one state the database deliberately never stores. */
export type EffectiveJobState = JobState | "PROCESSING_UNAVAILABLE" | "NOT_RUN";

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
  /** How the collector described its source when it ran. */
  source_attribution: string | null;
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
  /** Equals `state`, except a QUEUED job with no worker reads PROCESSING_UNAVAILABLE. */
  effective_state: string;
  /** False when no Celery worker answered a ping. */
  processing_available: boolean;
}

export interface RunResponse {
  job: Job;
  dispatch: Record<string, unknown>;
}

/**
 * Which settings a collector uses and whether they are present.
 *
 * Names and status only — the API never returns a credential value, so there is
 * nothing here that could leak one into the DOM or a log.
 */
export interface CollectorConfiguration {
  required_settings: string[];
  optional_settings: string[];
  configured: boolean;
  /** Short label for the operating mode: "unauthenticated", "brave", … */
  mode: string;
  detail: string;
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
  configuration: CollectorConfiguration;
}

/**
 * What the backend would store for a raw input.
 *
 * `ambiguous` inputs have no inferable type: `type` is null and `candidates`
 * holds the types the investigator must choose between.
 */
/**
 * Optional context an investigator can attach to a PERSON target.
 *
 * Used only to judge candidates a public source already returned — never sent
 * to a source as an extra search term, and never stored as a finding.
 */
export interface PersonContext {
  known_usernames?: string[];
  profile_urls?: string[];
  /** Public sites the subject is known to publish. */
  websites?: string[];
  organizations?: string[];
  schools?: string[];
  /** A profession, not a job title at a named employer. */
  occupation?: string | null;
  /** Exact public identifiers — the strongest anchors available. */
  orcid?: string | null;
  github_username?: string | null;
  country?: string | null;
  city?: string | null;
}

export type AnalystDecisionValue = "CONFIRMED" | "REJECTED" | "UNRESOLVED" | "NEEDS_REVIEW";
export type DecisionSubject = "CANDIDATE" | "SOCIAL_PROFILE" | "IMAGE";
/** Whether the platform actually read these bytes. */
export type ImageFetchState = "FETCHED" | "REFERENCE_ONLY" | "BLOCKED";

export interface AnalystDecisionRecord {
  id: string;
  subject_type: DecisionSubject;
  subject_id: string;
  decision: AnalystDecisionValue;
  note: string | null;
  decided_by: string | null;
  decided_at: string;
}

export interface SocialProfileRecord {
  id: string;
  candidate_entity_id: string | null;
  platform: string;
  platform_label: string;
  handle: string | null;
  profile_url: string;
  display_name: string | null;
  bio: string | null;
  source_url: string | null;
  accessibility: string;
  server_fetchable: boolean;
  fetch_note: string | null;
  collector: string;
  evidence_class: string;
  /** Computed by the platform. An analyst decision never changes it. */
  confidence: number;
  match_reasons: string[];
  mismatch_reasons: string[];
  corroborated_by: string[];
  retrieved_at: string | null;
  /** The analyst's separate judgement, shown alongside rather than instead. */
  decision: AnalystDecisionRecord | null;
}

export interface ImageEvidenceRecord {
  id: string;
  candidate_entity_id: string | null;
  social_profile_id: string | null;
  image_url: string;
  source_page_url: string;
  platform: string | null;
  caption: string | null;
  context_text: string | null;
  fetch_state: ImageFetchState;
  /** Present only for FETCHED: a hash of bytes the platform read. */
  sha256: string | null;
  content_type: string | null;
  byte_length: number | null;
  width: number | null;
  height: number | null;
  redirects: string[];
  final_url: string | null;
  fetch_note: string | null;
  origin: string;
  evidence_class: string;
  retrieved_at: string | null;
  evidence_id: string | null;
  attributes: Record<string, unknown>;
  decision: AnalystDecisionRecord | null;
}

export interface CandidateGroup {
  entity_id: string | null;
  display_name: string;
  canonical_value: string;
  confidence: number;
  confidence_reasons: string[];
  match_reasons: string[];
  mismatch_reasons: string[];
  corroborated_by: string[];
  identity_established: boolean;
  social_profiles: SocialProfileRecord[];
  images: ImageEvidenceRecord[];
  decision: AnalystDecisionRecord | null;
}

/** One generated search, and why it is worth running. */
export interface ReconQuery {
  query: string;
  family: string;
  rationale: string;
  priority: number;
  anchors_used: string[];
}

export interface ReconQueryPlan {
  target_id: string;
  subject_name: string;
  queries: ReconQuery[];
  anchors_used: string[];
  /** States plainly that the platform will not run these itself. */
  execution: string;
}

/** A public result the investigator selected from their own search. */
export interface ImportedResult {
  id: string;
  url: string;
  title: string;
  snippet: string;
  query: string;
  engine: string;
  platform: string | null;
  platform_label: string | null;
  url_kind: string | null;
  handle: string | null;
  is_image: boolean;
  image_url: string | null;
  thumbnail_url: string | null;
  caption: string | null;
  /** api_fetched | page_fetched | investigator_imported */
  evidence_class: string;
  imported_at: string | null;
  confidence: number;
  evidence_sha256: string[];
}

export interface ManualResultInput {
  query: string;
  url: string;
  title?: string;
  snippet?: string;
  engine?: string;
  result_type?: string | null;
  notes?: string | null;
  image_url?: string | null;
  thumbnail_url?: string | null;
  caption?: string | null;
}

export interface NormalizationPreview {
  raw_input: string;
  type: TargetType | null;
  normalized_value: string;
  attributes: Record<string, unknown>;
  ambiguous: boolean;
  candidates: TargetType[];
  message: string;
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
