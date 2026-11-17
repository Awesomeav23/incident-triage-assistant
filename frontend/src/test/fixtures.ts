import type { TriageResponse } from "../types";

export function makeResult(overrides: Partial<TriageResponse> = {}): TriageResponse {
  return {
    root_cause_summary: "Client is pinned to a node IP; after failover nothing listens there.",
    severity: "critical",
    severity_score: 5,
    severity_reason: "All session reads fail.",
    suggested_fix: "Set `REDIS_HOST` to the primary endpoint DNS name.",
    next_steps: ["Check failover events", "Update the config"],
    matched_incident_ids: [1],
    confidence: 0.82,
    similar_incidents: [
      { id: 1, title: "Redis failover", service: "session-service", error_type: "ConnectionError", root_cause: "rc", fix: "fx", severity: "critical", family: "redis-failover-refused", similarity: 0.91, score: 0.03 },
      { id: 2, title: "Redis timeout", service: "cart", error_type: "TimeoutError", root_cause: "rc2", fix: "fx2", severity: "medium", family: "redis-slow-command-timeout", similarity: 0.78, score: 0.02 },
    ],
    no_close_match: false,
    search_mode: "hybrid",
    redacted_secrets: 0,
    latency_ms: { embed: 15, search: 4, llm: 7200, total: 7219 },
    model: "claude-opus-5-5",
    ...overrides,
  };
}

/** Mock fetch with a function from (url, init) to [status, body]. */
export function mockFetch(handler: (url: string, init?: RequestInit) => [number, unknown]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const [status, body] = handler(String(input), init);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}
