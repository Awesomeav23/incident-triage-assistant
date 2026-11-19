import type {
  FeedbackIn,
  FeedbackOut,
  Health,
  IncidentDetail,
  IncidentIn,
  IncidentPage,
  Sample,
  Severity,
  Stats,
  TriageRequest,
  TriageResponse,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Turn FastAPI's error body into one readable sentence. */
export function errorMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    // 422 validation errors: [{loc: ["body","log"], msg: "..."}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((p) => p !== "body" && p !== "query").join(".");
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  if (status === 502 || status === 503 || status === 504) return "The API is unreachable or the AI service failed. Is the backend running?";
  return `Request failed (HTTP ${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the API. Start it with: uvicorn app.main:app --port 8030");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, body));
  return body as T;
}

export const api = {
  health: () => request<Health>("/health"),
  samples: () => request<Sample[]>("/samples"),
  triage: (req: TriageRequest) =>
    request<TriageResponse>("/triage", { method: "POST", body: JSON.stringify(req) }),
  incidents: (params: { q?: string; severity?: Severity | ""; limit?: number; offset?: number; includeSynthetic?: boolean }) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.severity) qs.set("severity", params.severity);
    qs.set("limit", String(params.limit ?? 20));
    qs.set("offset", String(params.offset ?? 0));
    if (params.includeSynthetic === false) qs.set("include_synthetic", "false");
    return request<IncidentPage>(`/incidents?${qs}`);
  },
  feedback: (fb: FeedbackIn) =>
    request<FeedbackOut>("/feedback", { method: "POST", body: JSON.stringify(fb) }),
  stats: (recent = 10) => request<Stats>(`/stats?recent=${recent}`),
  incident: (id: number) => request<IncidentDetail>(`/incidents/${id}`),
  addIncident: (inc: IncidentIn) =>
    request<{ id: number; title: string }>("/index-incident", {
      method: "POST",
      body: JSON.stringify(inc),
    }),
};
