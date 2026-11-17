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
  incidents_indexed: number;
  llm_mode: "demo" | "claude";
}

export const DEMO_MODEL = "demo";
