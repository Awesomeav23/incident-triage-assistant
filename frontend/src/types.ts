// Mirrors the Pydantic schemas in app/models.py. If the API changes shape,
// TypeScript errors here are the early warning.

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export interface SimilarIncident {
  id: number;
  title: string;
  service: string;
  error_type: string;
  root_cause: string;
  fix: string;
  severity: Severity;
  family: string | null;
  similarity: number; // cosine, 0..1
  score: number;
}

export interface TriageRequest {
  log: string;
  service?: string;
  top_k?: number;
}

export interface TriageResponse {
  root_cause_summary: string;
  severity: Severity;
  severity_score: number;
  severity_reason: string;
  suggested_fix: string;
  next_steps: string[];
  matched_incident_ids: number[];
  confidence: number;
  similar_incidents: SimilarIncident[];
  no_close_match: boolean;
  search_mode: string;
  redacted_secrets: number;
  latency_ms: { embed: number; search: number; llm: number; total: number };
  model: string;
  run_id: number | null; // send feedback for this id
  duplicate_of: number | null; // set when the result was reused for a repeated log
  duplicate_count: number; // times the log was seen again after the first triage
}

export interface FeedbackIn {
  triage_run_id: number;
  correct: boolean;
  root_cause?: string;
  fix?: string;
  severity?: Severity;
  index?: boolean;
  title?: string;
  service?: string;
}

export interface FeedbackOut {
  id: number;
  triage_run_id: number;
  correct: boolean;
  indexed_as: number | null;
}

export interface RunSummary {
  id: number;
  created_at: string;
  model: string;
  severity: Severity;
  summary: string;
  duplicate_count: number;
  feedback: "correct" | "incorrect" | null;
}

export interface Stats {
  incidents_indexed: number;
  triage_requests: number;
  triage_runs: number;
  duplicates_suppressed: number;
  feedback_count: number;
  feedback_correct: number;
  feedback_accuracy: number | null;
  indexed_from_feedback: number;
  severity_counts: Record<Severity, number>;
  recent_runs: RunSummary[];
}

export interface IncidentSummary {
  id: number;
  title: string;
  service: string;
  error_type: string;
  severity: Severity;
  family: string | null;
  synthetic: boolean;
  tags: string[];
}

export interface IncidentDetail extends IncidentSummary {
  log_text: string;
  root_cause: string;
  fix: string;
}

export interface IncidentPage {
  items: IncidentSummary[];
  total: number;
}

export interface IncidentIn {
  title: string;
  service: string;
  error_type: string;
  log_text: string;
  root_cause: string;
  fix: string;
  severity: Severity;
  tags: string[];
}

export interface Sample {
  name: string;
  log: string;
}

export interface Health {
  status: string;
  db: "up" | "down";
  incidents_indexed: number;
  llm_mode: "demo" | "claude";
}

export const DEMO_MODEL = "demo";
